"""Player, comandos, reports do Palco e stage claim — contrato §9, ARCHITECTURE §4, DECISION 0006."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from karaoke.app import create_app
from tests.api.core_helpers import SONG_A, SONG_B, SONG_C, seed_song, wait_for
from tests.conftest import BASE_URL

STATE_KEYS = {
    "revision",
    "status",
    "queue_item_id",
    "song_id",
    "song",
    "singer",
    "position_ms",
    "position_at",
    "duration_ms",
    "volume",
    "guide_vocal",
    "offset_ms",
    "autoplay",
    "up_next",
    "stage",
}
OWNER = "c_ab12"


def cmd(client, action, value=None, status=200):
    body = {"action": action, "client_id": OWNER}
    if value is not None:
        body["value"] = value
    r = client.post("/api/player/command", json=body)
    assert r.status_code == status, r.text
    return r.json()


def report(client, event="tick", position_ms=0, client_id=OWNER, playing=True, **extra):
    st = client.get("/api/player").json()
    body = {
        "client_id": client_id,
        "queue_item_id": st["queue_item_id"],
        "song_id": st["song_id"],
        "position_ms": position_ms,
        "playing": playing,
        "event": event,
        **extra,
    }
    r = client.post("/api/player/report", json=body)
    assert r.status_code == 204
    return client.get("/api/player").json()


@pytest.fixture
def playing(client, app):
    """Palco dono + fila com A (tocando), B e C pendentes."""
    for s, title in ((SONG_A, "Song A"), (SONG_B, "Song B"), (SONG_C, "Song C")):
        seed_song(app, s, title=title)
    client.post("/api/stage/claim", json={"client_id": OWNER})
    items = [
        client.post("/api/queue", json={"song_id": s, "singer": s[:3]}).json()["id"]
        for s in (SONG_A, SONG_B, SONG_C)
    ]
    st = report(client, "loaded", 0)
    assert st["status"] == "playing" and st["queue_item_id"] == items[0]
    return items


def test_initial_state(client):
    st = client.get("/api/player").json()
    assert set(st) == STATE_KEYS
    assert st["status"] == "idle" and st["song"] is None and st["queue_item_id"] is None
    assert st["volume"] == 0.8 and st["guide_vocal"] == 0.0 and st["autoplay"] is True
    assert st["stage"] == {"connected": 0, "audio_owner": None, "unlocked": False}


def test_play_without_stage_is_409_and_waiting_gesture(client, app):
    client.patch("/api/settings", json={"autoplay": False})
    seed_song(app, SONG_A)
    client.post("/api/queue", json={"song_id": SONG_A})
    assert client.get("/api/player").json()["status"] == "idle"
    r = client.post("/api/player/command", json={"action": "play"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "stage_not_connected"
    st = client.get("/api/player").json()
    assert st["status"] == "waiting_gesture" and st["song_id"] == SONG_A
    # claim após o gesto: waiting_gesture → loading; o claim vence
    st = client.post("/api/stage/claim", json={"client_id": OWNER}).json()
    assert st["status"] == "loading"
    assert st["stage"]["audio_owner"] == OWNER and st["stage"]["unlocked"] is True


def test_last_claim_wins(client, playing):
    st = client.post("/api/stage/claim", json={"client_id": "c_other"}).json()
    assert st["stage"]["audio_owner"] == "c_other"
    # report do antigo dono é ignorado
    before = client.get("/api/player").json()
    after = report(client, "ended", 1000, client_id=OWNER)
    assert after["queue_item_id"] == before["queue_item_id"] and after["status"] == "playing"


def test_loaded_singer_song_and_report_tick(client, playing):
    st = client.get("/api/player").json()
    assert st["song"]["id"] == SONG_A and st["singer"] == SONG_A[:3] and st["duration_ms"] == 200_000
    st = report(client, "tick", 63250)
    assert st["position_ms"] == 63250 and st["revision"] == client.get("/api/player").json()["revision"]
    # report de item antigo é ignorado
    r = client.post(
        "/api/player/report",
        json={
            "client_id": OWNER,
            "queue_item_id": "q_999",
            "position_ms": 5,
            "playing": True,
            "event": "tick",
        },
    )
    assert r.status_code == 204
    assert client.get("/api/player").json()["position_ms"] == 63250
    # report malformado
    assert client.post("/api/player/report", json={"client_id": "x"}).status_code == 400


def test_pause_play_toggle(client, playing):
    rev = client.get("/api/player").json()["revision"]
    st = cmd(client, "pause")
    assert st["status"] == "paused" and st["revision"] > rev
    assert cmd(client, "play")["status"] == "playing"
    assert cmd(client, "toggle")["status"] == "paused"
    assert cmd(client, "toggle")["status"] == "playing"


def test_seek_and_restart(client, playing):
    cmd(client, "pause")
    assert cmd(client, "seek", 90000)["position_ms"] == 90000
    assert cmd(client, "seek", 999_999)["position_ms"] == 200_000  # clamp em duration
    assert cmd(client, "seek", -5)["position_ms"] == 0
    cmd(client, "seek", 10000)
    assert cmd(client, "seek_relative", 5000)["position_ms"] == 15000
    assert cmd(client, "seek_relative", -50000)["position_ms"] == 0
    cmd(client, "seek", 10000)
    assert cmd(client, "restart")["position_ms"] == 0
    cmd(client, "seek", "abc", status=400)


def test_volume_guide_vocal_persist_in_settings(client, playing):
    assert cmd(client, "volume", 0.5)["volume"] == 0.5
    assert cmd(client, "volume_relative", 0.05)["volume"] == 0.55
    assert cmd(client, "volume", 3)["volume"] == 1.0
    assert cmd(client, "guide_vocal", 0.3)["guide_vocal"] == 0.3
    assert cmd(client, "guide_vocal_relative", -0.1)["guide_vocal"] == 0.2
    s = client.get("/api/settings").json()
    assert s["volume"] == 1.0 and s["guide_vocal_default"] == 0.2
    cmd(client, "volume", True, status=400)


def test_offset_updates_song(client, playing):
    assert cmd(client, "offset", -150)["offset_ms"] == -150
    assert cmd(client, "offset_relative", -50)["offset_ms"] == -200
    assert cmd(client, "offset", 20000)["offset_ms"] == 10000
    assert client.get(f"/api/songs/{SONG_A}").json()["offset_ms"] == 10000
    # PATCH da música atual reflete no player
    client.patch(f"/api/songs/{SONG_A}", json={"offset_ms": 40})
    assert client.get("/api/player").json()["offset_ms"] == 40


def test_next_prev_start(client, playing):
    a, b, c = playing
    st = cmd(client, "next")
    assert st["queue_item_id"] == b and st["status"] == "loading"
    q = client.get("/api/queue").json()
    assert q["history"][0]["id"] == a and q["history"][0]["status"] == "skipped"
    # prev com posição <= 3 s volta ao item anterior; o atual volta ao topo da fila
    st = cmd(client, "prev")
    assert st["queue_item_id"] == a
    assert [i["id"] for i in client.get("/api/queue").json()["items"]] == [b, c]
    # prev com posição > 3 s reinicia
    report(client, "loaded", 0)
    cmd(client, "seek", 5000)
    st = cmd(client, "prev")
    assert st["queue_item_id"] == a and st["position_ms"] == 0
    # start de um item específico
    st = cmd(client, "start", c)
    assert st["queue_item_id"] == c
    assert client.get("/api/queue").json()["history"][0]["id"] == a
    cmd(client, "start", "q_999", status=404)
    cmd(client, "start", 5, status=400)


def test_start_not_ready_item(client, app, playing):
    seed_song(app, "aaaaaaaaaaa", status="processing")
    q = client.post("/api/queue", json={"song_id": "aaaaaaaaaaa"}).json()["id"]
    r = client.post("/api/player/command", json={"action": "start", "value": q})
    assert r.status_code == 409 and r.json()["error"]["code"] == "song_not_ready"


def test_ended_goes_to_up_next_and_countdown_starts_next(client, playing):
    a, b, _ = playing
    client.patch("/api/settings", json={"countdown_s": 1})
    st = report(client, "ended", 199_000)
    assert st["status"] == "up_next" and st["queue_item_id"] is None
    assert st["up_next"]["queue_item_id"] == b and st["up_next"]["song"]["id"] == SONG_B
    assert st["up_next"]["singer"] == SONG_B[:3] and isinstance(st["up_next"]["starts_at"], int)
    assert client.get("/api/queue").json()["history"][0] == {
        **client.get("/api/queue").json()["history"][0],
        "id": a,
        "status": "done",
    }
    st = wait_for(lambda: (s := client.get("/api/player").json())["status"] == "loading" and s, timeout=3)
    assert st["queue_item_id"] == b and st["up_next"] is None


def test_up_next_without_autoplay_waits_for_play(client, playing):
    _, b, _ = playing
    cmd(client, "autoplay", False)
    st = report(client, "ended", 199_000)
    assert st["status"] == "up_next" and st["up_next"]["starts_at"] is None
    assert cmd(client, "play")["queue_item_id"] == b


def test_countdown_zero_starts_immediately(client, playing):
    _, b, _ = playing
    client.patch("/api/settings", json={"countdown_s": 0})
    st = report(client, "ended", 199_000)
    assert st["status"] == "loading" and st["queue_item_id"] == b


def test_queue_exhausted_goes_to_ended(client, playing):
    client.patch("/api/settings", json={"countdown_s": 0})
    for _ in range(3):
        report(client, "loaded", 0)
        st = report(client, "ended", 199_000)
    assert st["status"] == "ended" and st["song"] is None


def test_report_error_skips(client, playing):
    a, b, _ = playing
    st = report(client, "error", 0, error={"code": "decode_failed", "message": "falhou"})
    assert st["queue_item_id"] == b
    assert client.get("/api/queue").json()["history"][0]["status"] == "skipped"


def test_remove_current_item_equals_next(client, playing):
    a, b, _ = playing
    assert client.delete(f"/api/queue/{a}").status_code == 204
    assert client.get("/api/player").json()["queue_item_id"] == b


def test_invalid_command(client):
    r = client.post("/api/player/command", json={"action": "explode"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"
    assert client.post("/api/player/command", json={"action": "autoplay", "value": "yes"}).status_code == 400
    assert client.post("/api/stage/claim", json={"client_id": "nope"}).status_code == 400


def test_owner_disconnect_pauses(client, app, playing):
    app.state.player.OWNER_GRACE_MS = 100  # dono nunca abriu SSE neste teste
    st = wait_for(
        lambda: (s := client.get("/api/player").json())["stage"]["audio_owner"] is None and s, timeout=3
    )
    assert st["status"] == "paused" and st["stage"]["unlocked"] is False
    r = client.post("/api/player/command", json={"action": "play"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "stage_not_connected"
    assert client.get("/api/player").json()["status"] == "waiting_gesture"


def test_state_persists_across_restart(config, client, playing):
    cmd(client, "volume", 0.4)
    cmd(client, "pause")
    cmd(client, "seek", 42000)
    with TestClient(create_app(config), base_url=BASE_URL, client=("127.0.0.1", 50000)) as c2:
        st = c2.get("/api/player").json()
        assert st["status"] == "paused" and st["queue_item_id"] == playing[0]
        assert st["position_ms"] == 42000 and st["volume"] == 0.4
        assert st["stage"]["audio_owner"] is None  # nenhum Palco após reinício


# ---- review H1 (rodada 1): trocar o dono do áudio / recarregar o Palco preserva a posição -------------------------
def test_claim_by_other_stage_keeps_position(client, playing):
    """Enter no Palco espelho transfere o som (DECISION 0006): o novo dono reporta `loaded` com a posição do seu
    engine recém-carregado (0), mas a música continua de onde estava no servidor."""
    st = report(client, "tick", 90_000)
    assert st["position_ms"] >= 90_000
    client.post("/api/stage/claim", json={"client_id": "c_new"})
    st = report(client, "loaded", 0, client_id="c_new")
    assert st["status"] == "playing"
    assert st["position_ms"] >= 90_000, st["position_ms"]
    assert st["stage"]["audio_owner"] == "c_new"


def test_reloaded_owner_paused_keeps_position(client, playing):
    """F5 no Palco com a música pausada: o `loaded` da aba recarregada não zera nem despausa."""
    report(client, "tick", 42_000)
    st = cmd(client, "pause")
    assert st["status"] == "paused"
    pos = st["position_ms"]
    assert pos >= 42_000
    client.post("/api/stage/claim", json={"client_id": OWNER})
    st = report(client, "loaded", 0, playing=False)
    assert st["status"] == "paused" and st["position_ms"] == pos


def test_loaded_on_new_item_still_takes_stage_position(client, playing):
    """Início de item (`loading`): continua adotando a posição reportada pelo Palco."""
    st = cmd(client, "next")
    assert st["status"] == "loading"
    st = report(client, "loaded", 1_500)
    assert st["status"] == "playing" and 1_500 <= st["position_ms"] < 5_000


def test_start_current_item_restarts_instead_of_stuck_loading(client, playing):
    a, _, _ = playing
    report(client, "tick", 30_000)
    st = cmd(client, "start", a)
    assert st["queue_item_id"] == a and st["status"] == "playing" and st["position_ms"] < 2_000


def test_failed_transaction_restores_player_memory(client, app, playing):
    """Estado do player em memória acompanha o ROLLBACK do banco (review low)."""
    from karaoke.services.library import transaction

    before = client.get("/api/player").json()
    with pytest.raises(RuntimeError), transaction(app):
        app.state.player.s["status"] = "ended"
        app.state.player.s["position_ms"] = 123
        raise RuntimeError("falha no meio da transação")
    after = client.get("/api/player").json()
    assert after["status"] == before["status"] == "playing"
    assert after["revision"] == before["revision"]
