"""Regras do PlayerService: posição extrapolada, "A seguir" reagindo à fila, validação de valores."""

from __future__ import annotations

import pytest

from karaoke.errors import ApiError
from karaoke.services.player import PlayerService, _clamp, _num, now_ms
from tests.api.core_helpers import SONG_A, SONG_B, SONG_C, seed_song


def test_num_and_clamp():
    assert _num(5, "x", integer=True) == 5 and _num(0.5, "x") == 0.5
    for bad in (True, "1", None):
        with pytest.raises(ApiError):
            _num(bad, "x")
    assert _clamp(2, 0, 1) == 1 and _clamp(-1, 0, 1) == 0


def test_position_extrapolates_only_when_playing(app, client):
    p: PlayerService = app.state.player
    p.s.update(status="playing", position_ms=1000, position_at=now_ms() - 500, duration_ms=10_000)
    assert 1490 <= p.position() <= 1600
    p.s.update(position_at=now_ms() - 60_000)
    assert p.position() == 10_000  # clamp na duração
    p.s.update(status="paused", position_ms=1000)
    assert p.position() == 1000


def _setup_up_next(app, client):
    for s in (SONG_A, SONG_B, SONG_C):
        seed_song(app, s)
    client.post("/api/stage/claim", json={"client_id": "c_s"})
    items = [client.post("/api/queue", json={"song_id": s}).json()["id"] for s in (SONG_A, SONG_B, SONG_C)]
    st = client.get("/api/player").json()
    for event in ("loaded", "ended"):
        client.post(
            "/api/player/report",
            json={"client_id": "c_s", "queue_item_id": st["queue_item_id"], "position_ms": 0, "event": event},
        )
    st = client.get("/api/player").json()
    assert st["status"] == "up_next" and st["up_next"]["queue_item_id"] == items[1]
    return items


def test_up_next_follows_queue_changes(app, client):
    _, b, c = _setup_up_next(app, client)
    starts_at = client.get("/api/player").json()["up_next"]["starts_at"]
    client.post(f"/api/queue/{c}/move", json={"direction": "top"})
    st = client.get("/api/player").json()
    assert st["up_next"]["queue_item_id"] == c and st["up_next"]["starts_at"] == starts_at
    client.delete("/api/queue")
    assert client.get("/api/player").json()["status"] == "ended"


def test_pause_freezes_countdown_and_autoplay_toggle(app, client):
    _setup_up_next(app, client)
    st = client.post("/api/player/command", json={"action": "pause"}).json()
    assert st["status"] == "up_next" and st["up_next"]["starts_at"] is None
    client.post("/api/player/command", json={"action": "autoplay", "value": False})
    st = client.post("/api/player/command", json={"action": "autoplay", "value": True}).json()
    assert isinstance(st["up_next"]["starts_at"], int)
    st = client.post("/api/player/command", json={"action": "next"}).json()
    assert st["status"] == "loading"
