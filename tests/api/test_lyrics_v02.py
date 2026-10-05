"""Letras v0.2 (ciclo 20261004-1742, T-001): modos, sincronizar pela voz, editor manual, detectar offset."""

from __future__ import annotations

import json

import pytest

from karaoke.app import create_app
from karaoke.jobs.runner import insert_candidates
from tests.fixtures.pipeline import (
    OTHER_ID,
    SONG_ID,
    assert_lyrics_invariants,
    insert_ready_song,
    whisper_segments,
)


@pytest.fixture
def app(config):
    a = create_app(config)
    a.state.runner_autostart = False
    return a


@pytest.fixture
def events(app):
    got = []
    app.state.bus.add_listener(lambda e: got.append((e.type, e.data)))
    return got


def cands(client):
    return client.get(f"/api/songs/{SONG_ID}/lyrics/candidates").json()


def write_transcript(config, segments=None):
    d = config.songs_dir / SONG_ID
    d.mkdir(parents=True, exist_ok=True)
    (d / "transcript.json").write_text(
        json.dumps({"version": 1, "segments": segments or whisper_segments(), "language": "en"})
    )


def add_whisper_candidate(app):
    from karaoke.jobs.runner import whisper_candidate

    with app.state.db.tx() as conn:
        (cid,) = insert_candidates(
            conn, SONG_ID, [whisper_candidate({"segments": whisper_segments()}, 359000)], 359000
        )
        conn.execute("UPDATE songs SET lyrics_candidate_id=? WHERE id=?", (cid, SONG_ID))
    return cid


# ---- settings / jobs ------------------------------------------------------------------------------------
def test_lyrics_mode_setting(client):
    assert client.get("/api/settings").json()["lyrics_mode"] == "aligned"
    assert client.patch("/api/settings", json={"lyrics_mode": "online"}).json()["lyrics_mode"] == "online"
    r = client.patch("/api/settings", json={"lyrics_mode": "karaoke"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"


def test_job_lyrics_mode_override(client, app):
    body = {"youtube_id": SONG_ID, "title": "Queen – Bohemian Rhapsody", "duration_ms": 359000}
    r = client.post("/api/jobs", json={**body, "lyrics_mode": "transcribe"})
    assert r.status_code == 201
    conn = app.state.db.connect()
    opts = json.loads(conn.execute("SELECT options_json FROM jobs").fetchone()[0])
    conn.close()
    assert opts["lyrics_mode"] == "transcribe"
    assert (
        client.post("/api/jobs", json={**body, "youtube_id": OTHER_ID, "lyrics_mode": "x"}).status_code == 400
    )


# ---- align -------------------------------------------------------------------------------------------------
def test_align_creates_job_from_selected_online(client, app):
    base = insert_ready_song(app.state.db)
    r = client.post(f"/api/songs/{SONG_ID}/lyrics/align")
    assert r.status_code == 202
    job = r.json()["job"]
    assert job["kind"] == "align" and job["stage"] == "queued" and job["song_id"] == SONG_ID
    conn = app.state.db.connect()
    opts = json.loads(conn.execute("SELECT options_json FROM jobs WHERE id=?", (job["id"],)).fetchone()[0])
    conn.close()
    assert opts["candidate_id"] == base
    # idempotente enquanto ativo
    assert client.post(f"/api/songs/{SONG_ID}/lyrics/align").json()["job"]["id"] == job["id"]


def test_align_with_whisper_selected_uses_online_text(client, app):
    base = insert_ready_song(app.state.db)
    add_whisper_candidate(app)
    r = client.post(f"/api/songs/{SONG_ID}/lyrics/align", json={})
    assert r.status_code == 202
    conn = app.state.db.connect()
    opts = json.loads(conn.execute("SELECT options_json FROM jobs").fetchone()[0])
    conn.close()
    assert opts["candidate_id"] == base


def test_align_without_official_text_searches_first(client, app):
    insert_ready_song(app.state.db, lrc=None)
    r = client.post(f"/api/songs/{SONG_ID}/lyrics/align")
    assert r.status_code == 202
    conn = app.state.db.connect()
    opts = json.loads(conn.execute("SELECT options_json FROM jobs").fetchone()[0])
    conn.close()
    assert opts["candidate_id"] is None and opts["search"] is True


def test_align_rejects_whisper_text_as_base(client, app):
    insert_ready_song(app.state.db, lrc=None)
    wid = add_whisper_candidate(app)
    r = client.post(f"/api/songs/{SONG_ID}/lyrics/align", json={"candidate_id": wid})
    assert r.status_code == 409 and r.json()["error"]["code"] == "lyrics_text_required"
    r = client.post(f"/api/songs/{SONG_ID}/lyrics/align", json={"candidate_id": "lrclib:999"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "candidate_not_found"
    assert client.post(f"/api/songs/{OTHER_ID}/lyrics/align").status_code == 404


def test_align_end_to_end_with_runner(client, app, config, events):
    base = insert_ready_song(app.state.db)
    write_transcript(config)
    client.post(f"/api/songs/{SONG_ID}/lyrics/align")
    app.state.runner.run_pending()
    c = cands(client)
    assert c["selected_id"].startswith("aligned:")
    al = c["candidates"][0]
    assert al["source"] == "aligned" and al["label"] == "Online · sincronizada pela voz"
    assert al["derived_from"] == base and al["timing"] == "word"
    assert al["align"]["anchored_lines"] == 2 and al["align"]["lines"] == 3
    assert [x["id"] for x in c["candidates"]][1] == base  # a anterior continua listada
    doc = client.get(f"/api/songs/{SONG_ID}/lyrics").json()
    assert doc["source"] == "aligned" and doc["lines"][2]["interpolated"] is True
    assert_lyrics_invariants(doc["lines"], 359000)
    labels = {x["source"]: x["label"] for x in c["candidates"]}
    assert labels["lrclib"] == "Online"


# ---- manual-timing ----------------------------------------------------------------------------------------
LINES = [
    {"text": "Is this the real life?", "start_ms": 12000},
    {"text": "Is this just fantasy?", "start_ms": 15500, "end_ms": 18000},
    {"text": "Mama, just killed a man", "start_ms": 45000},
]


def test_manual_timing_creates_derived_manual_and_updates_in_place(client, app, events):
    base = insert_ready_song(app.state.db)
    r = client.put(f"/api/songs/{SONG_ID}/lyrics/manual-timing", json={"lines": LINES})
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["source"] == "manual" and doc["candidate_id"] == "manual:1" and doc["timing"] == "word"
    assert [ln["start_ms"] for ln in doc["lines"]] == [12000, 15500, 45000]
    assert doc["lines"][1]["end_ms"] == 18000
    assert (
        doc["lines"][0]["end_ms"] == 12000 + 2850
    )  # sem end_ms: mantém a duração da linha base (5 palavras × 450 + 600)
    # palavras redistribuídas proporcionalmente dentro da linha
    w = doc["lines"][2]["words"]
    assert [x["text"] for x in w] == ["Mama,", "just", "killed", "a", "man"]
    assert (
        w[0]["start_ms"] == 45000
        and w[1]["start_ms"] - w[0]["start_ms"] > w[4]["start_ms"] - w[3]["start_ms"]
    )
    assert_lyrics_invariants(doc["lines"], 359000)
    assert ("lyrics.updated", {"song_id": SONG_ID, "candidate_id": "manual:1", "source": "manual"}) in events
    c = cands(client)
    man = c["candidates"][0]
    assert man["label"] == "Manual" and man["derived_from"] == base and man["edited"] is True
    assert {x["id"] for x in c["candidates"]} == {"manual:1", base}

    # salvar de novo a manual do editor atualiza no lugar (200), mantém derived_from
    lines2 = [dict(LINES[0], start_ms=11900), *LINES[1:]]
    r = client.put(
        f"/api/songs/{SONG_ID}/lyrics/manual-timing", json={"candidate_id": "manual:1", "lines": lines2}
    )
    assert r.status_code == 200 and r.json()["candidate_id"] == "manual:1"
    assert r.json()["lines"][0]["start_ms"] == 11900
    c = cands(client)
    assert len(c["candidates"]) == 2 and c["candidates"][0]["derived_from"] == base


def test_manual_timing_keeps_words_of_base_when_shifted(client, app, config):
    insert_ready_song(app.state.db)
    write_transcript(config)
    client.post(f"/api/songs/{SONG_ID}/lyrics/align")
    app.state.runner.run_pending()
    doc = client.get(f"/api/songs/{SONG_ID}/lyrics").json()
    shifted = [{"text": ln["text"], "start_ms": ln["start_ms"] + 100} for ln in doc["lines"]]
    new = client.put(f"/api/songs/{SONG_ID}/lyrics/manual-timing", json={"lines": shifted}).json()
    a, b = doc["lines"][0]["words"], new["lines"][0]["words"]
    assert [x["start_ms"] + 100 for x in a] == [x["start_ms"] for x in b]
    assert not any("interpolated" in ln for ln in new["lines"])


@pytest.mark.parametrize(
    ("lines", "reason", "line"),
    [
        ([{"text": "a", "start_ms": 5000}, {"text": "b", "start_ms": 5000}], "not_sorted", 1),
        ([{"text": "a", "start_ms": 360000}], "after_end", 0),
        ([{"text": "a", "start_ms": 1000, "end_ms": 900}], "end_before_start", 0),
        ([{"text": "   ", "start_ms": 1000}], "empty_text", 0),
        (
            [{"text": "a b", "start_ms": 1000, "words": [{"text": "a", "start_ms": 1000, "end_ms": 1100}]}],
            "words_mismatch",
            0,
        ),
        (
            [
                {
                    "text": "a b",
                    "start_ms": 1000,
                    "words": [
                        {"text": "a", "start_ms": 1500, "end_ms": 1600},
                        {"text": "b", "start_ms": 1000, "end_ms": 1100},
                    ],
                }
            ],
            "words_not_sorted",
            0,
        ),
    ],
)
def test_manual_timing_validation(client, app, lines, reason, line):
    insert_ready_song(app.state.db)
    r = client.put(f"/api/songs/{SONG_ID}/lyrics/manual-timing", json={"lines": lines})
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "invalid_timing" and err["details"] == {"line": line, "reason": reason}
    assert len(cands(client)["candidates"]) == 1  # nada gravado


def test_manual_timing_request_limits(client, app):
    insert_ready_song(app.state.db)
    url = f"/api/songs/{SONG_ID}/lyrics/manual-timing"
    assert client.put(url, json={"lines": []}).status_code == 400
    assert client.put(url, json={"lines": [{"text": "x", "start_ms": -1}]}).status_code == 400
    many = [{"text": "x", "start_ms": i} for i in range(2001)]
    assert client.put(url, json={"lines": many}).status_code == 400
    assert client.put(url, json={"lines": [{"text": "x" * 501, "start_ms": 0}]}).status_code == 400
    r = client.put(url, json={"candidate_id": "manual:77", "lines": [{"text": "x", "start_ms": 0}]})
    assert r.status_code == 404 and r.json()["error"]["code"] == "candidate_not_found"
    r = client.put(
        f"/api/songs/{OTHER_ID}/lyrics/manual-timing", json={"lines": [{"text": "x", "start_ms": 0}]}
    )
    assert r.status_code == 404


def test_manual_timing_without_any_lyrics(client, app):
    insert_ready_song(app.state.db, lrc=None)
    r = client.put(f"/api/songs/{SONG_ID}/lyrics/manual-timing", json={"lines": LINES})
    assert r.status_code == 201
    assert cands(client)["candidates"][0]["derived_from"] is None


# ---- detect-offset ------------------------------------------------------------------------------------------
def test_detect_offset_with_cached_transcript(client, app, config):
    base = insert_ready_song(app.state.db)
    write_transcript(config)
    r = client.post(f"/api/songs/{SONG_ID}/lyrics/detect-offset")
    assert r.status_code == 200
    body = r.json()
    assert body["candidate_id"] == base and body["song_id"] == SONG_ID and body["current_offset_ms"] == 0
    # voz em 10,0 s e 11,75 s para LRC em 12,34 s e 15,80 s → letra está atrasada: sugere offset negativo
    assert body["suggested_offset_ms"] < 0 and 0 <= body["confidence"] <= 1


def test_detect_offset_without_cache_queues_analyze(client, app, config, events):
    insert_ready_song(app.state.db)
    r = client.post(f"/api/songs/{SONG_ID}/lyrics/detect-offset", json={})
    assert r.status_code == 202 and r.json()["job"]["kind"] == "analyze"


def test_detect_offset_errors(client, app):
    insert_ready_song(app.state.db, lrc=None)
    r = client.post(f"/api/songs/{SONG_ID}/lyrics/detect-offset")
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_lyrics"
    r = client.post(f"/api/songs/{SONG_ID}/lyrics/detect-offset", json={"candidate_id": "lrclib:5"})
    assert r.status_code == 404


# ---- CR-001 (T-006) ----------------------------------------------------------------------------------------
PLAIN_TEXT = "[ar:Queen]\nIs this the real life?\n\nIs this just fantasy?\nMama, just killed a man\n"


def test_paste_plain_text_creates_text_source_and_align_job(client, app, config, events):
    base = insert_ready_song(app.state.db)
    write_transcript(config)
    r = client.post(f"/api/songs/{SONG_ID}/lyrics/manual", json={"lrc": PLAIN_TEXT})
    assert r.status_code == 202, r.text
    body = r.json()
    cand, job = body["candidate"], body["job"]
    assert cand["source"] == "manual" and cand["timing"] == "none" and cand["label"] == "Colada · só texto"
    assert cand["selectable"] is False and cand["official_text"] is True and cand["line_count"] == 3
    assert cand["artist_name"] == "Queen"
    assert job["kind"] == "align"
    assert cands(client)["selected_id"] == base  # a seleção só muda quando o alinhamento terminar
    r = client.put(f"/api/songs/{SONG_ID}/lyrics/selected", json={"candidate_id": cand["id"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "candidate_not_selectable"
    app.state.runner.run_pending()
    doc = client.get(f"/api/songs/{SONG_ID}/lyrics").json()
    assert doc["source"] == "aligned"
    assert [ln["text"] for ln in doc["lines"]] == [
        "Is this the real life?",
        "Is this just fantasy?",
        "Mama, just killed a man",
    ]
    assert doc["lines"][0]["start_ms"] == 10000 and doc["lines"][2]["interpolated"] is True
    al = cands(client)["candidates"][0]
    assert al["derived_from"] == cand["id"]


def test_paste_plain_text_on_song_not_ready_just_stores(client, app):
    from karaoke.db import utcnow

    now = utcnow()
    with app.state.db.tx() as conn:
        conn.execute(
            "INSERT INTO songs(id, title, status, created_at, updated_at) VALUES (?, 't', 'processing', ?, ?)",
            (SONG_ID, now, now),
        )
    r = client.post(f"/api/songs/{SONG_ID}/lyrics/manual", json={"lrc": "linha um\nlinha dois"})
    assert r.status_code == 202 and r.json()["job"] is None


def test_candidate_flags_and_whisper_label(client, app):
    insert_ready_song(app.state.db)
    add_whisper_candidate(app)
    c = {x["source"]: x for x in cands(client)["candidates"]}
    assert (
        c["whisper"]["label"] == "Extraída da voz (sem letra oficial)"
        and c["whisper"]["official_text"] is False
    )
    assert c["lrclib"]["official_text"] is True and c["lrclib"]["selectable"] is True


def test_settings_transcribe_is_alias_of_aligned(client):
    r = client.patch("/api/settings", json={"lyrics_mode": "transcribe"})
    assert r.status_code == 200 and r.json()["lyrics_mode"] == "aligned"
