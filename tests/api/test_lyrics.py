import pytest

from karaoke.app import create_app
from karaoke.db import utcnow
from tests.fixtures.pipeline import (
    ENHANCED_LRC,
    OTHER_ID,
    SONG_ID,
    assert_lyrics_invariants,
    insert_ready_song,
    lrclib_factory,
    lrclib_record,
    lrclib_transport,
)


@pytest.fixture
def app(config):
    a = create_app(config)
    a.state.runner_autostart = False
    a.state.lrclib_transport = lrclib_transport(search=[lrclib_record(500, 358), lrclib_record(501, 340)])
    a.state.lrclib_factory = lambda: lrclib_factory(a.state.lrclib_transport)()
    return a


@pytest.fixture
def events(app):
    got = []
    app.state.bus.add_listener(lambda e: got.append((e.type, e.data)))
    return got


def test_get_lyrics_document(client, app):
    cid = insert_ready_song(app.state.db)
    with app.state.db.tx() as conn:
        conn.execute("UPDATE songs SET offset_ms=-150")
    doc = client.get(f"/api/songs/{SONG_ID}/lyrics").json()
    assert doc["version"] == 1 and doc["song_id"] == SONG_ID and doc["candidate_id"] == cid
    assert doc["source"] == "lrclib" and doc["timing"] == "line" and doc["offset_ms"] == -150
    assert doc["duration_ms"] == 359000 and doc["lines"][1]["end_ms"] == 18200
    assert set(doc["meta"]) == {
        "track_name",
        "artist_name",
        "album_name",
        "provider_id",
        "model",
        "created_at",
    }
    assert_lyrics_invariants(doc["lines"], 359000)


def test_no_lyrics_and_404(client, app):
    insert_ready_song(app.state.db, lrc=None)
    doc = client.get(f"/api/songs/{SONG_ID}/lyrics").json()
    assert doc["source"] is None and doc["timing"] == "none" and doc["lines"] == []
    r = client.get(f"/api/songs/{OTHER_ID}/lyrics")
    assert r.status_code == 404 and r.json()["error"]["code"] == "song_not_found"
    assert client.get(f"/api/songs/{OTHER_ID}/lyrics/candidates").status_code == 404


def test_manual_select_and_candidates(client, app, events):
    first = insert_ready_song(app.state.db)
    r = client.post(f"/api/songs/{SONG_ID}/lyrics/manual", json={"lrc": ENHANCED_LRC})
    assert r.status_code == 201
    doc = r.json()
    assert doc["source"] == "manual" and doc["candidate_id"] == "manual:1" and doc["timing"] == "word"
    assert_lyrics_invariants(doc["lines"])
    assert ("lyrics.updated", {"song_id": SONG_ID, "candidate_id": "manual:1", "source": "manual"}) in events
    song_ev = [d for t, d in events if t == "song.updated"][-1]
    assert song_ev["lyrics"]["source"] == "manual" and song_ev["lyrics"]["candidates"] == 2

    cands = client.get(f"/api/songs/{SONG_ID}/lyrics/candidates").json()
    assert cands["selected_id"] == "manual:1"
    assert [c["id"] for c in cands["candidates"]] == ["manual:1", first]
    lr = cands["candidates"][1]
    assert lr["source"] == "lrclib" and lr["duration_diff_ms"] == -1000 and lr["line_count"] == 3
    assert lr["preview"] == ["Is this the real life?", "Is this just fantasy?", "Mama, just killed a man"]
    assert lr["track_name"] == "Bohemian Rhapsody"

    r = client.put(f"/api/songs/{SONG_ID}/lyrics/selected", json={"candidate_id": first})
    assert r.status_code == 200 and r.json()["candidate_id"] == first
    r = client.put(f"/api/songs/{SONG_ID}/lyrics/selected", json={"candidate_id": "whisper:9"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "candidate_not_found"
    assert client.put(f"/api/songs/{SONG_ID}/lyrics/selected", json={"candidate_id": "x"}).status_code == 400


def test_candidate_of_other_song_not_selectable(client, app):
    insert_ready_song(app.state.db, OTHER_ID)
    cid = insert_ready_song(app.state.db, SONG_ID)
    other = client.get(f"/api/songs/{OTHER_ID}/lyrics").json()["candidate_id"]
    assert other != cid
    assert (
        client.put(f"/api/songs/{SONG_ID}/lyrics/selected", json={"candidate_id": other}).status_code == 404
    )


def test_manual_invalid_lrc(client, app):
    insert_ready_song(app.state.db)
    # só tags e marcadores vazios: nem tempo nem texto (texto puro agora é aceito — CR-001, test_lyrics_v02)
    r = client.post(f"/api/songs/{SONG_ID}/lyrics/manual", json={"lrc": "[ar:Queen]\n[00:01.00]\n"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_lrc"


def test_research_lrclib(client, app):
    insert_ready_song(app.state.db, lrc=None)
    r = client.post(
        f"/api/songs/{SONG_ID}/lyrics/search", json={"track_name": "Bohemian", "artist_name": "Queen"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["added"] == 2 and [c["duration_diff_ms"] for c in body["candidates"]] == [-1000, -19000]
    assert any("track_name=Bohemian" in c for c in app.state.lrclib_transport.calls)
    # rebuscar de novo não duplica; seleção não muda
    again = client.post(f"/api/songs/{SONG_ID}/lyrics/search", json={}).json()
    assert again["added"] == 0 and len(again["candidates"]) == 2
    assert client.get(f"/api/songs/{SONG_ID}/lyrics").json()["source"] is None


def test_research_network_error(client, app):
    insert_ready_song(app.state.db)
    app.state.lrclib_transport = lrclib_transport(fail=True)
    r = client.post(f"/api/songs/{SONG_ID}/lyrics/search")
    assert r.status_code == 502 and r.json()["error"]["code"] == "network_unavailable"


def test_research_lrclib_down_has_own_message(client, app):
    """QA M-04: 503 persistente do LRCLIB não é "sem internet" (mesmo código do contrato, mensagem própria)."""
    import httpx

    from karaoke import lrclib

    insert_ready_song(app.state.db)
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(503)

    t = httpx.MockTransport(handler)
    app.state.lrclib_factory = lambda: lrclib.LrclibClient(transport=t, sleep=lambda _s: None)
    r = client.post(f"/api/songs/{SONG_ID}/lyrics/search")
    err = r.json()["error"]
    assert r.status_code == 502 and err["code"] == "network_unavailable"
    assert "LRCLIB não respondeu" in err["message"] and "internet" not in err["message"]
    assert err["details"] == {"reason": "lrclib_unavailable"}
    assert len(calls) == 1 + len(lrclib.RETRY_BACKOFF_S)  # repetiu antes de desistir


def test_research_lrclib_transient_503_recovers(client, app):
    import httpx

    from karaoke import lrclib

    insert_ready_song(app.state.db, lrc=None)
    first = {"done": False}

    def handler(request):
        if not first["done"]:
            first["done"] = True
            return httpx.Response(503)
        if request.url.path == "/api/search":
            return httpx.Response(200, json=[lrclib_record(900, 358)])
        return httpx.Response(404)

    t = httpx.MockTransport(handler)
    app.state.lrclib_factory = lambda: lrclib.LrclibClient(transport=t, sleep=lambda _s: None)
    r = client.post(f"/api/songs/{SONG_ID}/lyrics/search")
    assert r.status_code == 200, r.text
    assert r.json()["added"] >= 1


def test_transcribe_creates_job(client, app):
    insert_ready_song(app.state.db)
    r = client.post(f"/api/songs/{SONG_ID}/lyrics/transcribe", json={"language": "pt"})
    assert r.status_code == 202
    job = r.json()["job"]
    assert job["kind"] == "transcribe" and job["stage"] == "queued" and job["song_id"] == SONG_ID
    again = client.post(f"/api/songs/{SONG_ID}/lyrics/transcribe")
    assert again.json()["job"]["id"] == job["id"]
    assert (
        client.post(f"/api/songs/{SONG_ID}/lyrics/transcribe", json={"language": "Português"}).status_code
        == 400
    )


def test_transcribe_requires_ready(client, app):
    now = utcnow()
    with app.state.db.tx() as conn:
        conn.execute(
            "INSERT INTO songs(id, title, status, created_at, updated_at) VALUES (?, 't', 'processing', ?, ?)",
            (SONG_ID, now, now),
        )
    r = client.post(f"/api/songs/{SONG_ID}/lyrics/transcribe")
    assert r.status_code == 409 and r.json()["error"]["code"] == "song_not_ready"
