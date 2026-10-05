"""`POST /api/jobs` e `GET /api/jobs/{id}` de convidado (T-004, ADR-0003 §5–§6, api.md §0.1 ¹²³ e §5).

Metadados só do servidor (cache da busca → probe), `409 long_video_not_allowed`, cotas, `enqueue` forçado,
`requested_by = g_<n>`, `Song.added_by = "host"`, `options.max_duration_ms` e `video_too_long` no download."""

from __future__ import annotations

import json

import pytest
from fastapi import Request

from karaoke import security
from karaoke import youtube as yt
from karaoke.app import create_app
from karaoke.config import Config
from karaoke.db import Database, utcnow
from karaoke.jobs import models
from karaoke.jobs import runner as runner_mod
from karaoke.jobs.runner import JobRunner
from karaoke.security import HOST, Principal
from tests.api.core_helpers import SONG_A, SONG_B, SONG_C, SONG_D, seed_job, seed_song

BIA = Principal(role="guest", id="g_1", name="Bia")
CAIO = Principal(role="guest", id="g_2", name="Caio")
NEW_ID = "aaaaaaaaaaa"


@pytest.fixture
def app(config):
    a = create_app(config)
    a.state.runner_autostart = False
    return a


@pytest.fixture(autouse=True)
def _clean_cache():
    yt._recent.clear()
    yield
    yt._recent.clear()


class As:
    def __init__(self, app):
        self.p: Principal = HOST

        def fake_authorize(request: Request) -> Principal:
            request.state.principal = self.p
            return self.p

        app.dependency_overrides[security.authorize] = fake_authorize

    def __call__(self, p: Principal) -> None:
        self.p = p


@pytest.fixture
def as_(app, client):
    assert client.patch("/api/settings", json={"autoplay": False}).status_code == 200
    return As(app)


@pytest.fixture
def probes(monkeypatch):
    """`fetch_info` falso: devolve metadados por id e conta as chamadas."""
    calls: list[str] = []
    meta = {
        SONG_A: ("Queen – Bohemian Rhapsody (Official Video)", "Queen Official", 359_000, False),
        SONG_B: ("Long – Concert", "Band", 1_260_000, False),
        SONG_C: ("Live now", "TV", None, True),
        SONG_D: ("Rick Astley - Never Gonna Give You Up", "Rick", 213_000, False),
        NEW_ID: ("Artist - Song", "Artist", 180_000, False),
    }

    def fake(vid):
        calls.append(vid)
        title, channel, dur, live = meta[vid]
        return yt.SearchResult(vid, title, channel, dur, live)

    monkeypatch.setattr(yt, "fetch_info", fake)
    return calls


def post(client, youtube_id=SONG_A, **body):
    return client.post("/api/jobs", json={"youtube_id": youtube_id, **body})


def test_guest_job_uses_server_metadata_and_forces_enqueue(client, app, as_, probes):
    as_(BIA)
    r = post(
        client,
        title="Fake title",
        channel="Fake",
        duration_ms=1000,
        enqueue=False,
        confirm_long=True,
        lyrics_mode="online",
        singer="  Bia e Ana ",
    )
    assert r.status_code == 201
    body = r.json()
    job, song, item = body["job"], body["song"], body["queue_item"]
    assert job["requested_by"] == "g_1" and job["stage"] == "queued"
    assert song["original_title"] == "Queen – Bohemian Rhapsody (Official Video)"
    assert song["duration_ms"] == 359_000 and song["added_by"] == "host"
    assert item is not None and item["added_by"] == "g_1" and item["added_by_name"] == "Bia"
    assert item["singer"] == "Bia e Ana"
    conn = app.state.db.connect()
    try:
        row = conn.execute("SELECT requested_by, options_json FROM jobs WHERE id=?", (job["id"],)).fetchone()
    finally:
        conn.close()
    opts = json.loads(row["options_json"])
    assert row["requested_by"] == "g_1"
    assert opts["max_duration_ms"] == 900_000 and "lyrics_mode" not in opts
    assert probes == [SONG_A]


def test_guest_job_prefers_search_cache(client, app, as_, probes, monkeypatch):
    monkeypatch.setattr(
        yt, "search", lambda q, limit=15: [yt.SearchResult(NEW_ID, "Cached - Song", "Ch", 200_000, False)]
    )
    as_(BIA)
    assert client.get("/api/search", params={"q": "song"}).status_code == 200
    r = post(client, NEW_ID)
    assert r.status_code == 201 and r.json()["song"]["duration_ms"] == 200_000
    assert probes == []  # não consultou o YouTube de novo


def test_guest_singer_defaults_to_name(client, as_, probes):
    as_(BIA)
    item = post(client).json()["queue_item"]
    assert item["singer"] == "Bia"


def test_guest_long_video_not_allowed(client, as_, probes):
    as_(BIA)
    r = post(client, SONG_B, duration_ms=60_000, confirm_long=True)
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "long_video_not_allowed"
    assert err["details"] == {"duration_ms": 1_260_000, "max_ms": 900_000}
    assert err["message"] == "Vídeos com mais de 15 minutos não podem ser pedidos."


def test_guest_live_and_network(client, as_, probes, monkeypatch):
    as_(BIA)
    assert post(client, SONG_C).status_code == 400

    def net(vid):
        raise yt.YoutubeError("network_unavailable", "x")

    monkeypatch.setattr(yt, "fetch_info", net)
    r = post(client, SONG_D)
    assert r.status_code == 502 and r.json()["error"]["code"] == "network_unavailable"


def test_guest_has_no_active_jobs_or_pending_quota(client, app, as_, probes):
    """DECISION 0016: sem cotas. Com 3 pedidos na fila, Bia ainda cria 3 preparos novos."""
    for s in (SONG_B, SONG_C):
        seed_song(app, s, duration_ms=100_000)
    as_(BIA)
    for s in (SONG_B, SONG_C, SONG_B):
        assert client.post("/api/queue", json={"song_id": s}).status_code == 201
    for vid in (SONG_A, SONG_D, NEW_ID):
        assert post(client, vid).status_code == 201
    conn = app.state.db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE requested_by='g_1'").fetchone()[0] == 3
    finally:
        conn.close()


def test_guest_ready_song_only_enqueues(client, app, as_, probes):
    seed_song(app, SONG_A)
    as_(BIA)
    r = post(client)
    assert r.status_code == 200
    body = r.json()
    assert body["job"] is None and body["queue_item"]["added_by"] == "g_1"
    assert probes == []  # duração veio da biblioteca


def test_guest_ready_long_song_rejected(client, app, as_, probes):
    seed_song(app, SONG_B, duration_ms=1_000_000)
    as_(BIA)
    assert post(client, SONG_B).json()["error"]["code"] == "long_video_not_allowed"


def test_guest_joins_active_job_of_other_actor(client, app, as_, probes):
    seed_song(app, SONG_A, status="processing", duration_ms=200_000)
    seed_job(app, SONG_A, "j_9", stage="separating")
    as_(BIA)
    # mesmo com a cota de preparos cheia, entrar num job ativo de outro ator não conta
    for vid in (SONG_D, NEW_ID):
        assert post(client, vid).status_code == 201
    r = post(client)
    assert r.status_code == 200
    body = r.json()
    assert body["job"] is None  # job do host: invisível ao convidado
    assert body["queue_item"]["added_by"] == "g_1" and body["queue_item"]["ready"] is False


def test_get_job_only_own(client, app, as_, probes):
    as_(BIA)
    mine = post(client).json()["job"]["id"]
    as_(HOST)
    host_job = post(client, SONG_D).json()["job"]["id"]
    assert client.get(f"/api/jobs/{mine}").json()["requested_by"] == "g_1"  # host vê todos
    as_(BIA)
    assert client.get(f"/api/jobs/{mine}").status_code == 200
    r = client.get(f"/api/jobs/{host_job}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "job_not_found"
    as_(CAIO)
    assert client.get(f"/api/jobs/{mine}").status_code == 404


def test_host_job_unchanged(client, as_, probes):
    r = post(client, enqueue=False)
    assert r.status_code == 201
    body = r.json()
    assert body["job"]["requested_by"] == "host" and body["queue_item"] is None
    assert body["song"]["added_by"] == "host"


# ---- defesa em profundidade: download -------------------------------------------------------------------
class _FakeYDL:
    duration = 1_000.0

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        info = {"id": "x", "duration": self.duration}
        mf = self.opts.get("match_filter")
        if mf is not None and mf(info, incomplete=False):
            return None  # yt-dlp pula o vídeo sem baixar
        return info


def test_download_rejects_video_too_long(tmp_path, monkeypatch):
    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYDL)
    monkeypatch.setattr(yt, "_base_opts", dict)
    with pytest.raises(yt.YoutubeError) as exc:
        yt.download_audio(SONG_A, tmp_path / "dl", max_duration_ms=900_000)
    assert exc.value.code == "video_too_long"
    assert "video_too_long" in runner_mod.MESSAGES
    # sem limite: segue o fluxo normal (aqui, sem arquivo → download_failed)
    with pytest.raises(yt.YoutubeError) as exc:
        yt.download_audio(SONG_A, tmp_path / "dl")
    assert exc.value.code == "download_failed"


def test_runner_passes_max_duration_and_fails_video_too_long(tmp_path, monkeypatch):
    """`options.max_duration_ms` do job chega ao `download_audio`; o erro vira `job.error.code = video_too_long`."""
    config = Config(port=8765, data_dir=tmp_path / "data", device="cpu", open_browser=False)
    config.ensure_dirs()
    db = Database(config.db_path)
    db.migrate()
    runner = JobRunner(config, db, _NullBus(), autostart=False)
    runner._device = ("cpu", "cpu")
    monkeypatch.setattr(models, "is_downloaded", lambda cfg, mid: True)
    seen: list[dict] = []

    def fake_download(song_id, dest, progress=None, cancel=None, **kw):
        seen.append(kw)
        if kw.get("max_duration_ms"):
            raise yt.YoutubeError("video_too_long", runner_mod.MESSAGES["video_too_long"])
        raise yt.YoutubeError("download_failed", "x")

    monkeypatch.setattr(yt, "download_audio", fake_download)
    now = utcnow()
    with db.tx() as conn:
        for sid in (SONG_A, SONG_D):
            conn.execute(
                "INSERT INTO songs(id, title, status, created_at, updated_at) VALUES (?, 't', 'queued', ?, ?)",
                (sid, now, now),
            )
        guest_job = runner.insert_job(conn, "process", SONG_A, "g_1", {"max_duration_ms": 900_000})
        host_job = runner.insert_job(conn, "process", SONG_D, "host")
    runner.run_pending()
    assert seen == [{"max_duration_ms": 900_000}, {}]
    g, h = runner.job_dict(guest_job), runner.job_dict(host_job)
    assert g["stage"] == "error" and g["error"]["code"] == "video_too_long" and g["requested_by"] == "g_1"
    assert h["error"]["code"] == "download_failed" and h["requested_by"] == "host"


class _NullBus:
    def publish(self, type_, data):
        return None
