import pytest

from karaoke import system
from karaoke.app import create_app
from karaoke.jobs import models
from tests.fixtures.pipeline import insert_ready_song


@pytest.fixture
def app(config, monkeypatch):
    monkeypatch.setattr(
        system, "probe_gpu", lambda: {"available": False, "error": "teste", "ct2_cuda_devices": 0}
    )
    a = create_app(config)
    a.state.runner_autostart = False
    return a


def test_system_reports_library_and_models(client, app):
    insert_ready_song(app.state.db)
    body = client.get("/api/system").json()
    assert body["device"] == "cpu" and body["model_download"] is None
    assert body["library"] == {"songs": 1, "size_mb": 0}
    ids = [(m["id"], m["kind"], m["active"]) for m in body["models"]]
    assert ("htdemucs_ft", "separation", True) in ids and ("small", "transcription", True) in ids
    assert all(m["downloaded"] is False for m in body["models"])
    assert {"ffmpeg", "yt_dlp", "js_runtime"} <= set(body["tools"])


def test_model_download_endpoint(client, monkeypatch):
    r = client.post("/api/system/models/nope/download")
    assert r.status_code == 404 and r.json()["error"]["code"] == "model_not_found"
    monkeypatch.setattr(models, "is_downloaded", lambda cfg, mid: True)
    r = client.post("/api/system/models/large-v3-turbo/download")
    assert r.status_code == 200 and r.json() == {"model_id": "large-v3-turbo", "status": "downloaded"}


def test_model_download_starts_in_background(client, app, monkeypatch):
    import threading

    done = threading.Event()
    monkeypatch.setattr(models, "is_downloaded", lambda cfg, mid: False)

    def fake(cfg, mid, on_progress=None, cancel=None):
        models._set_current({"model_id": mid, "progress": 0.5})
        on_progress(0.5)
        models._set_current(None)
        done.set()

    monkeypatch.setattr(models, "download", fake)
    got = []
    app.state.bus.add_listener(lambda e: got.append(e.type))
    r = client.post("/api/system/models/small/download")
    assert r.status_code == 202 and r.json() == {"model_id": "small", "status": "downloading"}
    assert done.wait(5)
    assert "system.updated" in got or "toast" in got


def test_vocal_sigs_from_bag():
    bag = {
        "models": ["a", "b", "c", "d"],
        "weights": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
    }
    assert models.vocal_sigs_from_bag(bag) == ["d"]
    assert models.vocal_sigs_from_bag({"models": ["x"]}) == ["x"]
