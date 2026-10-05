import pytest

from karaoke import youtube as yt
from karaoke.app import create_app
from karaoke.db import utcnow
from tests.fixtures.pipeline import OTHER_ID, SONG_ID, insert_ready_song


@pytest.fixture
def app(config):
    a = create_app(config)
    a.state.runner_autostart = False
    return a


def results():
    return [
        yt.SearchResult(SONG_ID, "Queen – Bohemian Rhapsody", "Queen Official", 359000, False),
        yt.SearchResult(OTHER_ID, "Long mix", "DJ", 20 * 60 * 1000, False),
        yt.SearchResult("aaaaaaaaaaa", "Live now", "TV", None, True),
    ]


def test_search_marks_library_long_and_live(client, app, monkeypatch):
    seen = {}

    def fake(q, limit):
        seen.update(q=q, limit=limit)
        return results()

    monkeypatch.setattr(yt, "search", fake)
    insert_ready_song(app.state.db)
    now = utcnow()
    with app.state.db.tx() as conn:
        conn.execute(
            "INSERT INTO songs(id, title, status, created_at, updated_at) VALUES (?, 'x', 'processing', ?, ?)",
            (OTHER_ID, now, now),
        )
        app.state.runner.insert_job(conn, "process", OTHER_ID, "host")
    r = client.get("/api/search", params={"q": "  bohemian   rhapsody "})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "bohemian rhapsody" and seen == {"q": "bohemian rhapsody", "limit": 15}
    a, b, c = body["results"]
    assert a == {
        "youtube_id": SONG_ID,
        "title": "Queen – Bohemian Rhapsody",
        "channel": "Queen Official",
        "duration_ms": 359000,
        "thumbnail": f"/api/thumbs/{SONG_ID}",
        "url": f"https://www.youtube.com/watch?v={SONG_ID}",
        "is_long": False,
        "is_live": False,
        "library": {"status": "ready", "job_id": None},
    }
    assert b["is_long"] is True and b["library"] == {"status": "processing", "job_id": "j_1"}
    assert c["is_live"] is True and c["library"] is None


@pytest.mark.parametrize("q", ["", "   ", "x" * 201])
def test_search_invalid(client, q):
    r = client.get("/api/search", params={"q": q})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"


def test_search_limit_bounds(client):
    assert client.get("/api/search", params={"q": "a", "limit": 26}).status_code == 400


@pytest.mark.parametrize(
    ("err", "code"),
    [
        (yt.YoutubeError("network_unavailable", "sem rede"), "network_unavailable"),
        (yt.YoutubeError("youtube_error", "quebrou", yt.UPDATE_HINT), "youtube_error"),
    ],
)
def test_search_errors(client, monkeypatch, err, code):
    def boom(q, limit):
        raise err

    monkeypatch.setattr(yt, "search", boom)
    r = client.get("/api/search", params={"q": "abc"})
    assert r.status_code == 502
    e = r.json()["error"]
    assert e["code"] == code and e["message"]
    if code == "youtube_error":
        assert "make update-ytdlp" in e["details"]["hint"]


def test_thumbs_proxy(client, app, tmp_path):
    img = tmp_path / "t.jpg"
    img.write_bytes(b"\xff\xd8x")

    class FakeCache:
        def get(self, yid):
            return img if yid == SONG_ID else None

    app.state.thumb_cache = FakeCache()
    r = client.get(f"/api/thumbs/{SONG_ID}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content == b"\xff\xd8x"
    assert client.get(f"/api/thumbs/{OTHER_ID}").status_code == 404
    assert client.get("/api/thumbs/bad").status_code in (400, 404)
