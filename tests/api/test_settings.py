"""Configurações — contrato §3 (`/api/settings`)."""

from __future__ import annotations

DEFAULTS = {
    "countdown_s": 8,
    "autoplay": True,
    "guide_vocal_default": 0.0,
    "volume": 0.8,
    "separation_model": "htdemucs_ft",
    "whisper_model": "large-v3-turbo",
    "long_video_warn_s": 900,
    "theme": "system",
    "lyrics_mode": "aligned",
}


def test_defaults(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert r.json() == DEFAULTS


def test_patch_subset_returns_full_object(client):
    r = client.patch("/api/settings", json={"countdown_s": 5, "theme": "dark"})
    assert r.status_code == 200
    assert r.json() == {**DEFAULTS, "countdown_s": 5, "theme": "dark"}
    assert client.get("/api/settings").json()["theme"] == "dark"


def test_patch_validation(client):
    for body in (
        {"countdown_s": 31},
        {"volume": 1.5},
        {"theme": "pink"},
        {"separation_model": "x"},
        {"unknown": 1},
        {"volume": None},
    ):
        r = client.patch("/api/settings", json=body)
        assert r.status_code == 400, body
        assert r.json()["error"]["code"] == "invalid_request"


def test_patch_syncs_player(client):
    client.patch("/api/settings", json={"volume": 0.3, "autoplay": False, "guide_vocal_default": 0.5})
    st = client.get("/api/player").json()
    assert st["volume"] == 0.3 and st["autoplay"] is False and st["guide_vocal"] == 0.5


def test_empty_patch(client):
    assert client.patch("/api/settings", json={}).json() == DEFAULTS
