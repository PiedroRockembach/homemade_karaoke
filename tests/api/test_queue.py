"""Fila — contrato §8 (inclui regra de posse com dois atores, DECISION 0009/0012)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from karaoke.app import create_app
from tests.api.core_helpers import SONG_A, SONG_B, SONG_C, SONG_D, Actor, seed_job, seed_song
from tests.conftest import BASE_URL

ITEM_KEYS = {
    "id",
    "song_id",
    "singer",
    "status",
    "ready",
    "song",
    "job",
    "added_by",
    "can_remove",
    "added_at",
}


@pytest.fixture
def actor(monkeypatch):
    return Actor(monkeypatch)


@pytest.fixture
def no_autoplay(client):
    """Desliga o autoplay para a fila não iniciar sozinha (testes de ordenação)."""
    assert client.patch("/api/settings", json={"autoplay": False}).status_code == 200


def ids(snapshot):
    return [i["id"] for i in snapshot["items"]]


def test_get_empty_queue(client):
    r = client.get("/api/queue")
    assert r.status_code == 200
    assert r.json() == {"revision": 0, "current": None, "items": [], "history": []}


def test_add_item_contract_and_added_by(client, app, no_autoplay):
    seed_song(app, SONG_A)
    r = client.post("/api/queue", json={"song_id": SONG_A, "singer": "  Ana  ", "added_by": "guest:evil"})
    assert r.status_code == 201
    item = r.json()
    assert set(item) == ITEM_KEYS
    assert item["id"].startswith("q_") and item["status"] == "pending"
    assert item["singer"] == "Ana" and item["ready"] is True and item["job"] is None
    assert item["added_by"] == "host" and item["can_remove"] is True
    assert item["song"]["id"] == SONG_A
    snap = client.get("/api/queue").json()
    assert ids(snap) == [item["id"]] and snap["revision"] >= 1


def test_add_processing_song_shows_job(client, app, no_autoplay):
    seed_song(app, SONG_B, status="processing")
    seed_job(app, SONG_B, "j_3", stage="separating", progress=0.57)
    item = client.post("/api/queue", json={"song_id": SONG_B}).json()
    assert item["ready"] is False
    assert item["job"] == {"stage": "separating", "progress": 0.57}
    assert item["singer"] is None


def test_add_errors(client, app):
    seed_song(app, SONG_C, status="error")
    r = client.post("/api/queue", json={"song_id": SONG_C})
    assert r.status_code == 409 and r.json()["error"]["code"] == "song_not_ready"
    r = client.post("/api/queue", json={"song_id": SONG_D})
    assert r.status_code == 404 and r.json()["error"]["code"] == "song_not_found"
    assert client.post("/api/queue", json={"song_id": "bad"}).status_code == 400
    seed_song(app, SONG_A)
    assert client.post("/api/queue", json={"song_id": SONG_A, "position": -1}).status_code == 400
    assert client.post("/api/queue", json={"song_id": SONG_A, "singer": "x" * 81}).status_code == 400


def test_positions(client, app, no_autoplay):
    for s in (SONG_A, SONG_B, SONG_C, SONG_D):
        seed_song(app, s)
    a = client.post("/api/queue", json={"song_id": SONG_A}).json()["id"]
    b = client.post("/api/queue", json={"song_id": SONG_B, "position": "end"}).json()["id"]
    c = client.post("/api/queue", json={"song_id": SONG_C, "position": "next"}).json()["id"]
    d = client.post("/api/queue", json={"song_id": SONG_D, "position": 1}).json()["id"]
    assert ids(client.get("/api/queue").json()) == [c, d, a, b]
    e = client.post("/api/queue", json={"song_id": SONG_A, "position": 99}).json()["id"]
    assert ids(client.get("/api/queue").json())[-1] == e


def test_reorder_and_stale(client, app, no_autoplay):
    for s in (SONG_A, SONG_B, SONG_C):
        seed_song(app, s)
    q = [client.post("/api/queue", json={"song_id": s}).json()["id"] for s in (SONG_A, SONG_B, SONG_C)]
    rev = client.get("/api/queue").json()["revision"]
    r = client.post("/api/queue/reorder", json={"revision": rev, "item_ids": [q[2], q[0], q[1]]})
    assert r.status_code == 200
    assert ids(r.json()) == [q[2], q[0], q[1]] and r.json()["revision"] == rev + 1
    # revisão desatualizada
    r = client.post("/api/queue/reorder", json={"revision": rev, "item_ids": q})
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_queue"
    # lista que não bate com os pendentes
    r = client.post("/api/queue/reorder", json={"item_ids": q[:2]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"
    # sem revisão = sem checagem
    assert ids(client.post("/api/queue/reorder", json={"item_ids": q}).json()) == q


def test_move(client, app, no_autoplay):
    for s in (SONG_A, SONG_B, SONG_C):
        seed_song(app, s)
    q = [client.post("/api/queue", json={"song_id": s}).json()["id"] for s in (SONG_A, SONG_B, SONG_C)]
    assert ids(client.post(f"/api/queue/{q[2]}/move", json={"direction": "top"}).json()) == [q[2], q[0], q[1]]
    assert ids(client.post(f"/api/queue/{q[2]}/move", json={"direction": "down"}).json()) == [
        q[0],
        q[2],
        q[1],
    ]
    assert ids(client.post(f"/api/queue/{q[0]}/move", json={"direction": "up"}).json()) == [q[0], q[2], q[1]]
    assert ids(client.post(f"/api/queue/{q[0]}/move", json={"direction": "bottom"}).json()) == [
        q[2],
        q[1],
        q[0],
    ]
    assert ids(client.post(f"/api/queue/{q[0]}/move", json={"to_index": 0}).json()) == [q[0], q[2], q[1]]
    assert client.post(f"/api/queue/{q[0]}/move", json={"to_index": 0, "direction": "up"}).status_code == 400
    assert client.post(f"/api/queue/{q[0]}/move", json={}).status_code == 400
    assert client.post("/api/queue/q_999/move", json={"to_index": 0}).status_code == 404


def test_patch_singer(client, app, no_autoplay):
    seed_song(app, SONG_A)
    q = client.post("/api/queue", json={"song_id": SONG_A, "singer": "Ana"}).json()["id"]
    r = client.patch(f"/api/queue/{q}", json={"singer": "Bruno"})
    assert r.status_code == 200 and r.json()["singer"] == "Bruno"
    assert client.patch(f"/api/queue/{q}", json={"singer": None}).json()["singer"] is None
    r = client.patch("/api/queue/q_999", json={"singer": "x"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "queue_item_not_found"


def test_remove_and_clear(client, app, no_autoplay):
    for s in (SONG_A, SONG_B, SONG_C):
        seed_song(app, s)
    q = [client.post("/api/queue", json={"song_id": s}).json()["id"] for s in (SONG_A, SONG_B, SONG_C)]
    assert client.delete(f"/api/queue/{q[1]}").status_code == 204
    assert ids(client.get("/api/queue").json()) == [q[0], q[2]]
    assert client.delete(f"/api/queue/{q[1]}").status_code == 404
    assert client.delete("/api/queue").status_code == 204
    assert client.get("/api/queue").json()["items"] == []


def test_remove_owner_rule_two_actors(client, app, actor, no_autoplay):
    """Só quem adicionou remove — sem exceção para o host (DECISION 0012)."""
    seed_song(app, SONG_A)
    host_item = client.post("/api/queue", json={"song_id": SONG_A}).json()
    actor.guest()  # POST /api/queue é guest-safe
    guest_item = client.post("/api/queue", json={"song_id": SONG_A, "added_by": "host"}).json()
    assert guest_item["added_by"] == "guest:x" and guest_item["can_remove"] is True
    # can_remove por ator na listagem
    items = {i["id"]: i for i in client.get("/api/queue").json()["items"]}
    assert items[host_item["id"]]["can_remove"] is False and items[guest_item["id"]]["can_remove"] is True
    actor.host()
    items = {i["id"]: i for i in client.get("/api/queue").json()["items"]}
    assert items[host_item["id"]]["can_remove"] is True and items[guest_item["id"]]["can_remove"] is False
    # host não remove item do guest
    r = client.delete(f"/api/queue/{guest_item['id']}")
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_owner"
    # guest não remove item do host (a política é a mesma; a rota é host-only na v1, então chama o serviço)
    from karaoke.errors import ApiError

    with pytest.raises(ApiError) as exc:
        app.state.queue.remove(host_item["id"], "guest:x")
    assert exc.value.code == "not_owner"
    # o dono remove o seu
    app.state.queue.remove(guest_item["id"], "guest:x")
    assert client.delete(f"/api/queue/{host_item['id']}").status_code == 204
    assert client.get("/api/queue").json()["items"] == []


def test_clear_and_reorder_are_not_removal(client, app, actor, no_autoplay):
    """Limpar/reordenar não seguem a regra de posse (AC-05.5): o host limpa itens de outros."""
    seed_song(app, SONG_A)
    actor.guest()
    g = client.post("/api/queue", json={"song_id": SONG_A}).json()["id"]
    actor.host()
    h = client.post("/api/queue", json={"song_id": SONG_A}).json()["id"]
    assert ids(client.post("/api/queue/reorder", json={"item_ids": [h, g]}).json()) == [h, g]
    assert client.delete("/api/queue").status_code == 204
    assert client.get("/api/queue").json()["items"] == []


def test_autoplay_starts_ready_item_and_skips_not_ready(client, app):
    seed_song(app, SONG_B, status="processing")
    seed_song(app, SONG_A)
    pb = client.post("/api/queue", json={"song_id": SONG_B}).json()["id"]
    assert client.get("/api/player").json()["status"] == "idle"  # item não pronto não inicia
    pa = client.post("/api/queue", json={"song_id": SONG_A}).json()["id"]
    snap = client.get("/api/queue").json()
    assert snap["current"]["id"] == pa and snap["current"]["status"] == "playing"
    assert ids(snap) == [pb]  # o não pronto continua na fila
    player = client.get("/api/player").json()
    assert player["status"] == "waiting_gesture" and player["queue_item_id"] == pa


def test_song_ready_notification_starts_player(client, app):
    seed_song(app, SONG_B, status="processing")
    client.post("/api/queue", json={"song_id": SONG_B})
    with app.state.db.tx() as conn:
        conn.execute("UPDATE songs SET status = 'ready', media_version = 1 WHERE id = ?", (SONG_B,))
    app.state.queue.song_changed(SONG_B)  # interface para o JobRunner (T-006)
    snap = client.get("/api/queue").json()
    assert snap["current"]["song_id"] == SONG_B and snap["current"]["ready"] is True


def test_queue_persists_across_restart(config, app, client, no_autoplay):
    seed_song(app, SONG_A)
    seed_song(app, SONG_B)
    q = [
        client.post("/api/queue", json={"song_id": s, "singer": "Ana"}).json()["id"] for s in (SONG_A, SONG_B)
    ]
    client.close()
    with TestClient(create_app(config), base_url=BASE_URL, client=("127.0.0.1", 50000)) as c2:
        snap = c2.get("/api/queue").json()
        assert ids(snap) == q and snap["items"][0]["singer"] == "Ana"
        assert c2.get("/api/settings").json()["autoplay"] is False
