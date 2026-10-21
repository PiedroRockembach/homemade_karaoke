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
    "scoring_enabled": True,
    "extra_lyrics_sources": True,
    "vagalume_api_key_set": False,
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


# ---- ADR-0005: fontes extras e chave do Vagalume -------------------------------------------------------------
SECRET = "segredo-vagalume-987"


def test_extra_lyrics_sources_toggle(client):
    r = client.patch("/api/settings", json={"extra_lyrics_sources": False})
    assert r.status_code == 200 and r.json()["extra_lyrics_sources"] is False
    assert client.get("/api/settings").json()["extra_lyrics_sources"] is False


def test_vagalume_key_store_and_remove_never_returned(client, app, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    events = []
    app.state.bus.add_listener(lambda e: events.append((e.type, e.data)))
    r = client.patch("/api/settings", json={"vagalume_api_key": f" {SECRET} "})
    assert r.status_code == 200 and r.json()["vagalume_api_key_set"] is True
    assert "vagalume_api_key" not in r.json() and SECRET not in r.text
    assert SECRET not in client.get("/api/settings").text
    assert SECRET not in repr(events) and SECRET not in caplog.text
    from karaoke.api.settings import get_lyrics_provider_settings

    assert get_lyrics_provider_settings(app.state.db, app.state.config)["vagalume_api_key"] == SECRET
    for removal in ("", None):
        client.patch("/api/settings", json={"vagalume_api_key": SECRET})
        r = client.patch("/api/settings", json={"vagalume_api_key": removal})
        assert r.status_code == 200 and r.json()["vagalume_api_key_set"] is False


def test_vagalume_key_validation(client):
    r = client.patch("/api/settings", json={"vagalume_api_key_set": True})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"
    r = client.patch("/api/settings", json={"vagalume_api_key": "x" * 201})
    assert r.status_code == 400 and "x" * 201 not in r.text
    r = client.patch("/api/settings", json={"extra_lyrics_sources": None})
    assert r.status_code == 400
