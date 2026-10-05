"""Biblioteca — contrato §6 (inclui regra de posse com dois atores, DECISION 0009/0012)."""

from __future__ import annotations

import pytest

from tests.api.core_helpers import SONG_A, SONG_B, SONG_C, Actor, seed_job, seed_song

SONG_KEYS = {
    "id",
    "youtube_id",
    "title",
    "artist",
    "original_title",
    "channel",
    "duration_ms",
    "status",
    "job_id",
    "thumbnail",
    "lyrics",
    "offset_ms",
    "media",
    "size_bytes",
    "added_by",
    "can_remove",
    "created_at",
}


@pytest.fixture
def actor(monkeypatch):
    return Actor(monkeypatch)


def test_list_empty(client):
    r = client.get("/api/songs")
    assert r.status_code == 200
    assert r.json() == {"songs": [], "total": 0}


def test_song_object_matches_contract(client, app):
    seed_song(app, SONG_A)
    r = client.get(f"/api/songs/{SONG_A}")
    assert r.status_code == 200
    song = r.json()
    assert set(song) == SONG_KEYS
    assert song["id"] == song["youtube_id"] == SONG_A
    assert song["status"] == "ready"
    assert song["thumbnail"] == f"/media/songs/{SONG_A}/thumb.jpg"
    assert song["media"] == {
        "instrumental": f"/media/songs/{SONG_A}/instrumental.opus?v=1",
        "vocals": f"/media/songs/{SONG_A}/vocals.opus?v=1",
        "version": 1,
    }
    assert song["lyrics"] == {"source": None, "timing": "none", "candidate_id": None, "candidates": 0}
    assert song["added_by"] == "host" and song["can_remove"] is True
    assert song["created_at"].endswith("Z")


def test_song_not_ready_and_lyrics_summary(client, app):
    seed_song(app, SONG_B, status="processing")
    seed_job(app, SONG_B, "j_7")
    song = client.get(f"/api/songs/{SONG_B}").json()
    assert song["media"] is None
    assert song["thumbnail"] == f"/api/thumbs/{SONG_B}"
    assert song["job_id"] == "j_7"

    seed_song(app, SONG_A)
    with app.state.db.tx() as conn:
        for cid, src, timing in (("lrclib:1", "lrclib", "line"), ("whisper:1", "whisper", "word")):
            conn.execute(
                "INSERT INTO lyrics_candidates(id, song_id, source, timing, lyrics_json, created_at) "
                "VALUES (?, ?, ?, ?, '{}', '2026-10-04T00:00:00Z')",
                (cid, SONG_A, src, timing),
            )
        conn.execute("UPDATE songs SET lyrics_candidate_id = 'whisper:1' WHERE id = ?", (SONG_A,))
    song = client.get(f"/api/songs/{SONG_A}").json()
    assert song["lyrics"] == {
        "source": "whisper",
        "timing": "word",
        "candidate_id": "whisper:1",
        "candidates": 2,
    }


def test_get_unknown_and_invalid_id(client):
    r = client.get(f"/api/songs/{SONG_C}")
    assert r.status_code == 404
    assert r.json() == {
        "error": {
            "code": "song_not_found",
            "message": "Música não encontrada.",
            "details": {"song_id": SONG_C},
        }
    }
    assert client.get("/api/songs/../etc").status_code in (400, 404)
    assert client.get("/api/songs/short").json()["error"]["code"] == "invalid_request"


def test_list_filters_sort_and_search(client, app):
    seed_song(app, SONG_A, title="Canção do Mar", artist="Dulce Pontes", created_at="2026-10-04T00:00:01Z")
    seed_song(app, SONG_B, title="Ainda Bem", artist="Marisa Monte", created_at="2026-10-04T00:00:02Z")
    seed_song(
        app, SONG_C, title="Zebra", artist="Ásia", status="processing", created_at="2026-10-04T00:00:03Z"
    )

    body = client.get("/api/songs").json()
    assert [s["id"] for s in body["songs"]] == [SONG_B, SONG_A]  # só prontas, mais recentes primeiro
    assert body["total"] == 2
    assert client.get("/api/songs?status=all").json()["total"] == 3
    # busca case/acento-insensível em título e artista
    assert [s["id"] for s in client.get("/api/songs?q=CANCAO").json()["songs"]] == [SONG_A]
    assert [s["id"] for s in client.get("/api/songs?q=asia&status=all").json()["songs"]] == [SONG_C]
    assert [s["id"] for s in client.get("/api/songs?q=marisa").json()["songs"]] == [SONG_B]
    assert [s["id"] for s in client.get("/api/songs?sort=title").json()["songs"]] == [SONG_B, SONG_A]
    by_artist = client.get("/api/songs?sort=artist&status=all").json()["songs"]
    assert [s["id"] for s in by_artist] == [SONG_C, SONG_A, SONG_B]
    assert client.get("/api/songs?sort=bogus").status_code == 400


def test_patch_song(client, app):
    seed_song(app, SONG_A)
    r = client.patch(f"/api/songs/{SONG_A}", json={"title": "  Bohemian   Rhapsody ", "offset_ms": -150})
    assert r.status_code == 200
    assert r.json()["title"] == "Bohemian Rhapsody" and r.json()["offset_ms"] == -150
    assert client.get(f"/api/songs/{SONG_A}").json()["offset_ms"] == -150
    assert client.patch(f"/api/songs/{SONG_A}", json={"offset_ms": 10001}).status_code == 400
    assert client.patch(f"/api/songs/{SONG_A}", json={"title": "  "}).status_code == 400
    assert client.patch(f"/api/songs/{SONG_A}", json={"added_by": "x"}).status_code == 400
    assert client.patch(f"/api/songs/{SONG_B}", json={"title": "x"}).status_code == 404


def test_delete_song_removes_files_and_pending_items(client, app):
    seed_song(app, SONG_A, files=True)
    seed_song(app, SONG_B)
    client.post("/api/queue", json={"song_id": SONG_A})  # inicia (waiting_gesture) → item atual
    client.post("/api/queue", json={"song_id": SONG_B})
    client.post("/api/queue", json={"song_id": SONG_A})
    # música atual em waiting_gesture não está "tocando": pode excluir
    assert (app.state.config.songs_dir / SONG_A).exists()
    r = client.delete(f"/api/songs/{SONG_A}")
    assert r.status_code == 204
    assert not (app.state.config.songs_dir / SONG_A).exists()
    assert client.get(f"/api/songs/{SONG_A}").status_code == 404
    q = client.get("/api/queue").json()
    assert all(i["song_id"] != SONG_A for i in q["items"])
    assert q["current"]["song_id"] == SONG_B  # player avançou para a próxima pronta


def test_delete_song_in_use_conflict(client, app):
    seed_song(app, SONG_A)
    client.post("/api/stage/claim", json={"client_id": "c_stage1"})
    client.post("/api/queue", json={"song_id": SONG_A})
    assert client.get("/api/player").json()["status"] == "loading"
    r = client.delete(f"/api/songs/{SONG_A}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "song_in_use"


def test_delete_unknown_song(client):
    assert client.delete(f"/api/songs/{SONG_A}").json()["error"]["code"] == "song_not_found"


def test_delete_song_owner_rule_two_actors(client, app, actor):
    seed_song(app, SONG_A, added_by="host")
    seed_song(app, SONG_B, added_by="guest:x")
    # can_remove calculado para o ator da requisição
    assert client.get(f"/api/songs/{SONG_B}").json()["can_remove"] is False
    actor.guest()
    assert client.get(f"/api/songs/{SONG_B}").json()["can_remove"] is True
    assert client.get(f"/api/songs/{SONG_A}").json()["can_remove"] is False
    # guest não remove música do host (o guest não alcança a rota host: o teste chama a política direto)
    actor.host()
    # DECISION 0012: host não remove música adicionada pelo guest
    r = client.delete(f"/api/songs/{SONG_B}")
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_owner"
    assert client.get(f"/api/songs/{SONG_B}").status_code == 200
    assert client.delete(f"/api/songs/{SONG_A}").status_code == 204


def test_library_service_guest_cannot_delete_host_song(app, client):
    from karaoke.errors import ApiError

    seed_song(app, SONG_A, added_by="host")
    with pytest.raises(ApiError) as exc:
        app.state.library.delete(SONG_A, "guest:x")
    assert exc.value.code == "not_owner"


# ---- review M1: excluir música com job rodando não espera o subprocesso morrer segurando locks ---------------------
STUBBORN = (
    "import signal, sys, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    "sys.stdout.write('ok'); sys.stdout.flush(); time.sleep(30)"
)


def test_delete_song_with_running_job_does_not_block(client, app, monkeypatch):
    import subprocess
    import sys
    import time

    from karaoke.jobs import runner as runner_mod

    monkeypatch.setattr(runner_mod, "KILL_GRACE_S", 0.5)
    seed_song(app, SONG_A, status="processing")
    seed_job(app, SONG_A, "j_1", stage="separating")
    runner = app.state.runner
    ctx = runner_mod._Ctx("j_1", "process", SONG_A)
    # processo que ignora SIGTERM: antes, o DELETE ficava até 5 s parado com o lock de estado e do SQLite
    proc = subprocess.Popen([sys.executable, "-c", STUBBORN], stdout=subprocess.PIPE)
    try:
        assert proc.stdout.read(2) == b"ok"  # handler de SIGTERM já instalado
        ctx.proc = proc
        runner._current = ctx
        t0 = time.monotonic()
        r = client.delete(f"/api/songs/{SONG_A}")
        elapsed = time.monotonic() - t0
        assert r.status_code == 204, r.text
        assert elapsed < 0.4, elapsed
        assert ctx.cancel.is_set()
        # o SIGKILL vem depois, fora da requisição
        proc.wait(timeout=5)
        assert proc.returncode is not None
    finally:
        runner._current = None
        if proc.poll() is None:
            proc.kill()
            proc.wait()
