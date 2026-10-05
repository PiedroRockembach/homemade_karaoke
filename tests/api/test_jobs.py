import pytest

from karaoke import youtube as yt
from karaoke.app import create_app
from karaoke.db import utcnow
from tests.fixtures.pipeline import OTHER_ID, SONG_ID, insert_ready_song

BODY = {
    "youtube_id": SONG_ID,
    "title": "Queen – Bohemian Rhapsody (Official Video Remastered)",
    "channel": "Queen Official",
    "duration_ms": 359000,
}


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


def test_create_job(client, app, events):
    r = client.post("/api/jobs", json=BODY)
    assert r.status_code == 201
    body = r.json()
    job, song = body["job"], body["song"]
    assert body["queue_item"] is None
    assert job["id"] == "j_1" and job["kind"] == "process" and job["stage"] == "queued"
    assert job["queue_position"] == 1 and job["progress"] == 0 and job["error"] is None
    assert job["song"] == {
        "title": "Bohemian Rhapsody",
        "artist": "Queen",
        "thumbnail": f"/api/thumbs/{SONG_ID}",
    }
    assert song["title"] == "Bohemian Rhapsody" and song["artist"] == "Queen"
    assert song["original_title"] == BODY["title"] and song["status"] == "queued"
    assert song["added_by"] == "host" and song["can_remove"] is True and song["job_id"] == "j_1"
    assert ("job.updated", job) in events or any(t == "job.updated" for t, _ in events)
    conn = app.state.db.connect()
    assert conn.execute("SELECT requested_by FROM jobs WHERE id='j_1'").fetchone()[0] == "host"
    conn.close()


def test_create_job_is_idempotent_while_active(client):
    first = client.post("/api/jobs", json=BODY).json()["job"]
    r = client.post("/api/jobs", json=BODY)
    assert r.status_code == 200 and r.json()["job"]["id"] == first["id"]


def test_ready_song_not_reprocessed_and_enqueued(client, app):
    insert_ready_song(app.state.db)
    r = client.post("/api/jobs", json={**BODY, "enqueue": True, "singer": "Ana"})
    assert r.status_code == 200
    body = r.json()
    assert body["job"] is None and body["song"]["status"] == "ready"
    item = body["queue_item"]
    assert item["song_id"] == SONG_ID and item["singer"] == "Ana" and item["added_by"] == "host"


def test_prepare_and_enqueue_processing_song(client):
    r = client.post("/api/jobs", json={**BODY, "enqueue": True, "singer": "Bia"})
    assert r.status_code == 201
    item = r.json()["queue_item"]
    assert item["ready"] is False and item["singer"] == "Bia" and item["can_remove"] is True


def test_song_added_by_other_actor_not_removable_by_host(client, app):
    insert_ready_song(app.state.db, added_by="guest:x")
    song = client.post("/api/jobs", json=BODY).json()["song"]
    assert song["added_by"] == "guest:x" and song["can_remove"] is False


def test_long_video_requires_confirmation(client):
    body = {**BODY, "duration_ms": 16 * 60 * 1000}
    r = client.post("/api/jobs", json=body)
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "long_video_confirmation_required" and err["details"]["duration_ms"] == 960000
    assert client.post("/api/jobs", json={**body, "confirm_long": True}).status_code == 201


def test_metadata_fetched_when_missing(client, monkeypatch):
    monkeypatch.setattr(
        yt,
        "fetch_info",
        lambda vid: yt.SearchResult(vid, "Rick Astley - Never Gonna Give You Up", "Rick", 213000, False),
    )
    r = client.post("/api/jobs", json={"youtube_id": OTHER_ID})
    assert r.status_code == 201
    s = r.json()["song"]
    assert (s["title"], s["artist"], s["duration_ms"]) == ("Never Gonna Give You Up", "Rick Astley", 213000)


def test_live_and_unavailable_rejected(client, monkeypatch):
    monkeypatch.setattr(yt, "fetch_info", lambda vid: yt.SearchResult(vid, "Live", "TV", None, True))
    assert client.post("/api/jobs", json={"youtube_id": OTHER_ID}).status_code == 400

    def net(vid):
        raise yt.YoutubeError("network_unavailable", "x")

    monkeypatch.setattr(yt, "fetch_info", net)
    r = client.post("/api/jobs", json={"youtube_id": OTHER_ID})
    assert r.status_code == 502 and r.json()["error"]["code"] == "network_unavailable"


@pytest.mark.parametrize("bad", [{"youtube_id": "../etc/pass"}, {"youtube_id": "short"}, {}])
def test_invalid_body(client, bad):
    assert client.post("/api/jobs", json=bad).status_code == 400


def test_error_song_is_reprocessed(client, app):
    client.post("/api/jobs", json=BODY)
    now = utcnow()
    with app.state.db.tx() as conn:
        conn.execute(
            "UPDATE jobs SET stage='error', error_code='youtube_error', finished_at=? WHERE id='j_1'", (now,)
        )
        conn.execute("UPDATE songs SET status='error', error_code='youtube_error'")
    r = client.post("/api/jobs", json=BODY)
    assert r.status_code == 201 and r.json()["job"]["id"] == "j_2"
    assert r.json()["song"]["status"] == "queued"


def test_list_get_retry_delete(client, app, events):
    client.post("/api/jobs", json=BODY)
    client.post("/api/jobs", json={**BODY, "youtube_id": OTHER_ID})
    jobs = client.get("/api/jobs").json()["jobs"]
    assert [(j["id"], j["queue_position"]) for j in jobs] == [("j_1", 1), ("j_2", 2)]
    assert client.get("/api/jobs/j_1").json()["id"] == "j_1"
    r = client.get("/api/jobs/j_99")
    assert r.status_code == 404 and r.json()["error"]["code"] == "job_not_found"
    assert client.get("/api/jobs", params={"status": "bogus"}).status_code == 400

    r = client.post("/api/jobs/j_1/retry")
    assert r.status_code == 409 and r.json()["error"]["code"] == "job_not_retryable"
    assert client.delete("/api/jobs/j_1").status_code == 204  # queued -> canceled
    assert client.get("/api/jobs/j_1").json()["stage"] == "canceled"
    assert [j["id"] for j in client.get("/api/jobs").json()["jobs"]] == ["j_2"]
    assert [j["id"] for j in client.get("/api/jobs", params={"status": "recent"}).json()["jobs"]] == [
        "j_2",
        "j_1",
    ]
    r = client.post("/api/jobs/j_1/retry")
    assert r.status_code == 200 and r.json()["stage"] == "queued" and r.json()["queue_position"] == 2
    assert client.delete("/api/jobs/j_1").status_code == 204
    assert client.delete("/api/jobs/j_1").status_code == 204  # canceled -> removido
    assert client.get("/api/jobs/j_1").status_code == 404
    assert ("job.removed", {"id": "j_1"}) in events
    assert client.delete("/api/jobs/j_1").status_code == 404
    assert client.post("/api/jobs/j_99/retry").status_code == 404


def test_hello_interfaces_exposed(client, app):
    client.post("/api/jobs", json=BODY)
    assert [j["id"] for j in app.state.jobs_snapshot("host")] == ["j_1"]
    assert app.state.model_download is None


def test_delete_song_cancels_job(client, app):
    client.post("/api/jobs", json=BODY)
    assert client.delete(f"/api/songs/{SONG_ID}").status_code == 204
    assert client.get("/api/jobs/j_1").status_code == 404
