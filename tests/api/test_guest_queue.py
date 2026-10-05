"""Fila com convidado (T-004, ADR-0003 §5–§6, api.md §0.1 ⁴⁵ e §6/§8): `added_by_name`, singer padrão, só `end`,
sem cota (DECISION 0016), DELETE `guest-safe` com posse e `409 queue_item_playing`, `409 song_queued_by_others`.

O convidado é injetado por override do `authorize` (não depende do bloco A1)."""

from __future__ import annotations

import pytest
from fastapi import Request

from karaoke import security
from karaoke.security import HOST, Principal
from tests.api.core_helpers import SONG_A, SONG_B, SONG_C, SONG_D, seed_song

BIA = Principal(role="guest", id="g_1", name="Bia")
CAIO = Principal(role="guest", id="g_2", name="Caio")


class As:
    def __init__(self, app):
        self.p: Principal = HOST

        def fake_authorize(request: Request) -> Principal:
            request.state.principal = self.p
            return self.p

        app.dependency_overrides[security.authorize] = fake_authorize

    def __call__(self, p: Principal) -> None:
        self.p = p


@pytest.fixture
def as_(app):
    return As(app)


@pytest.fixture
def no_autoplay(client):
    assert client.patch("/api/settings", json={"autoplay": False}).status_code == 200


def add(client, song_id=SONG_A, **body):
    return client.post("/api/queue", json={"song_id": song_id, **body})


def test_guest_add_sets_owner_name_and_default_singer(client, app, as_, no_autoplay):
    seed_song(app, SONG_A)
    as_(BIA)
    r = add(client, added_by="host", added_by_name="Hacker")
    assert r.status_code == 201
    item = r.json()
    assert item["added_by"] == "g_1" and item["added_by_name"] == "Bia"
    assert item["singer"] == "Bia" and item["can_remove"] is True
    # singer editável (saneado), mas added_by_name é sempre o da sessão
    item2 = add(client, singer="  Ana‮  ").json()
    assert item2["singer"] == "Ana" and item2["added_by_name"] == "Bia"
    # a listagem e o host também veem o nome
    as_(HOST)
    items = client.get("/api/queue").json()["items"]
    assert [i["added_by_name"] for i in items] == ["Bia", "Bia"]
    host_item = add(client).json()
    assert host_item["added_by"] == "host" and host_item["added_by_name"] is None


@pytest.mark.parametrize("position", ["next", 0, 2])
def test_guest_position_only_end(client, app, as_, no_autoplay, position):
    seed_song(app, SONG_A)
    as_(BIA)
    r = add(client, position=position)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"
    assert add(client, position="end").status_code == 201


def test_guest_singer_too_long(client, app, as_, no_autoplay):
    seed_song(app, SONG_A)
    as_(BIA)
    r = add(client, singer="x" * 41)
    assert r.status_code == 400 and "singer" in r.json()["error"]["details"]["fields"]


def test_guest_has_no_pending_items_quota(client, app, as_, no_autoplay):
    """DECISION 0016: sem cota por convidado. Bia pede 6 músicas seguidas e todas entram."""
    for s in (SONG_A, SONG_B, SONG_C, SONG_D):
        seed_song(app, s)
    as_(BIA)
    for s in (SONG_A, SONG_B, SONG_C, SONG_D, SONG_A, SONG_B):
        assert add(client, s).status_code == 201
    conn = app.state.db.connect()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM queue_items WHERE added_by='g_1' AND status='pending'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 6


def test_guest_delete_own_pending_and_ownership(client, app, as_, no_autoplay):
    seed_song(app, SONG_A)
    host_item = add(client).json()["id"]
    as_(BIA)
    mine = add(client).json()["id"]
    as_(CAIO)
    other = add(client).json()["id"]
    as_(BIA)
    r = client.delete(f"/api/queue/{host_item}")
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_owner"
    r = client.delete(f"/api/queue/{other}")
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_owner"
    assert client.delete(f"/api/queue/{mine}").status_code == 204
    assert client.delete(f"/api/queue/{mine}").status_code == 404
    remaining = {i["id"] for i in client.get("/api/queue").json()["items"]}
    assert remaining == {host_item, other}


def test_guest_cannot_remove_own_playing_item(client, app, as_, no_autoplay):
    seed_song(app, SONG_A)
    as_(BIA)
    mine = add(client).json()["id"]
    with app.state.db.tx() as conn:
        conn.execute("UPDATE queue_items SET status='playing' WHERE id=?", (mine,))
    r = client.delete(f"/api/queue/{mine}")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "queue_item_playing"
    assert r.json()["error"]["details"] == {"queue_item_id": mine}


def test_up_next_has_added_by_name(client, app, as_, no_autoplay):
    seed_song(app, SONG_A)
    as_(BIA)
    item = add(client).json()["id"]
    player = app.state.player
    player.s["up_next"] = {"queue_item_id": item, "starts_at": None}
    conn = app.state.db.connect()
    try:
        st = player.state_conn(conn, "host")
    finally:
        conn.close()
        player.s["up_next"] = None
    assert st["up_next"]["added_by_name"] == "Bia" and st["up_next"]["singer"] == "Bia"


def test_delete_song_queued_by_others(client, app, as_, no_autoplay):
    seed_song(app, SONG_A)
    as_(BIA)
    mine = add(client).json()["id"]
    as_(HOST)
    add(client)  # item do próprio host não bloqueia
    r = client.delete(f"/api/songs/{SONG_A}")
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "song_queued_by_others" and err["details"]["count"] == 1
    as_(BIA)
    assert client.delete(f"/api/queue/{mine}").status_code == 204
    as_(HOST)
    assert client.delete(f"/api/songs/{SONG_A}").status_code == 204
    assert client.get("/api/queue").json()["items"] == []


def test_host_flow_unchanged(client, app, as_, no_autoplay):
    """Host continua com `next`/índice, sem cota e sem `added_by_name`."""
    seed_song(app, SONG_A)
    a = add(client).json()["id"]
    b = add(client, position="next").json()
    assert b["added_by"] == "host" and b["added_by_name"] is None and b["singer"] is None
    assert [i["id"] for i in client.get("/api/queue").json()["items"]] == [b["id"], a]
    assert client.delete(f"/api/queue/{a}").status_code == 204
