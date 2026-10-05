"""Ponto único de autorização (ADR-0002) e regra de posse (SPEC AC-04.4/AC-05.5, DECISION 0009/0012)."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

from karaoke import security
from karaoke.security import (
    HOST,
    Principal,
    can_remove,
    current_actor,
    declared_scope,
    ensure_can_remove,
    iter_api_routes,
    scope,
)
from tests.conftest import BASE_URL

GUEST_X = Principal(role="guest", id="guest:x", name="X")


# ---- toda rota declara escopo ----------------------------------------------------------------
def test_every_route_declares_scope(app):
    routes = list(iter_api_routes(app))
    assert len(routes) >= 40  # garante que routers incluídos foram percorridos
    missing = [
        f"{sorted(r.methods)} {path}"
        for path, r in routes
        if (path.startswith(("/api/", "/media/", "/static/")) or path in ("/", "/stage"))
        and declared_scope(r) is None
    ]
    assert not missing, f"rotas sem escopo: {missing}"


def test_scope_table_examples(app):
    scopes = {(m, path): declared_scope(r) for path, r in iter_api_routes(app) for m in r.methods}
    assert scopes[("GET", "/api/health")] == "guest-safe"
    assert scopes[("POST", "/api/queue")] == "guest-safe"
    assert scopes[("DELETE", "/api/queue/{item_id}")] == "guest-safe"  # v0.3 (era host)
    assert scopes[("DELETE", "/api/songs/{song_id}")] == "host"
    assert scopes[("POST", "/api/player/command")] == "host"
    assert scopes[("GET", "/media/songs/{song_id}/vocals.opus")] == "host"
    assert scopes[("GET", "/media/songs/{song_id}/thumb.jpg")] == "guest-safe"
    assert scopes[("GET", "/api/songs/{song_id}/lyrics")] == "host"  # escopo vindo do APIRouter
    # v0.3: escopo public (ADR-0003 §4)
    assert scopes[("GET", "/guest")] == "public"
    assert scopes[("GET", "/static/{path:path}")] == "public"
    assert scopes[("POST", "/api/guest/session")] == "public"
    assert scopes[("GET", "/api/guest/session")] == "public"
    assert scopes[("DELETE", "/api/guest/session")] == "guest-safe"
    for path in ("/api/guest/enable", "/api/guest/disable", "/api/guest/rotate", "/api/guest/end-sessions"):
        assert scopes[("POST", path)] == "host"
    assert scopes[("GET", "/api/guest/state")] == "host"
    assert scopes[("GET", "/api/guest/qr")] == "host"


# ---- Host / Origin / Content-Type / loopback ---------------------------------------------------
def test_foreign_host_header_forbidden(client):
    r = client.get("/api/health", headers={"Host": "evil.com"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "[::1]:8765"])
def test_allowed_hosts(client, host):
    assert client.get("/api/health", headers={"Host": host}).status_code == 200


def test_wrong_port_host_forbidden(client):
    assert client.get("/api/health", headers={"Host": "127.0.0.1:9999"}).status_code == 403


def test_cross_origin_mutation_forbidden(client):
    r = client.post("/api/queue", json={"song_id": "fJ9rUzIMcZQ"}, headers={"Origin": "http://evil.com"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_same_origin_mutation_passes_authorize(client):
    r = client.post("/api/queue", json={"song_id": "fJ9rUzIMcZQ"}, headers={"Origin": BASE_URL})
    assert r.status_code != 403


def test_mutation_body_requires_json(client):
    r = client.post("/api/queue", content=b"song_id=x", headers={"Content-Type": "text/plain"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"


def test_non_loopback_client_forbidden(app):
    with TestClient(app, base_url=BASE_URL, client=("192.168.0.20", 40000)) as c:
        r = c.get("/api/health")
    assert r.status_code == 403


def test_guest_principal_blocked_on_host_route(client, monkeypatch):
    monkeypatch.setattr(security, "resolve_principal", lambda request: GUEST_X)
    assert client.post("/api/player/command", json={"action": "pause"}).status_code == 403
    assert client.get("/api/health").status_code == 200  # guest-safe continua acessível


# ---- regra de posse: can_remove ------------------------------------------------------------------
def test_can_remove_host_moderates_guest_only_own():
    host_item = {"id": "q_1", "added_by": "host"}
    guest_item = {"id": "q_2", "added_by": "guest:x"}
    assert can_remove("host", host_item)
    assert can_remove("guest:x", guest_item)
    assert not can_remove("guest:x", host_item)
    assert not can_remove("guest:y", guest_item)
    # DECISION 0006 (ciclo 20261005-0109): o host modera — remove item de convidado
    assert can_remove("host", guest_item), "host remove item adicionado por guest:x"


def test_can_remove_resource_shapes():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT 'guest:x' AS added_by").fetchone()
    assert can_remove("guest:x", row) and can_remove("host", row) and not can_remove("guest:y", row)
    assert can_remove("host", SimpleNamespace(added_by="host"))
    assert not can_remove("host", {"id": "x"})  # sem dono conhecido → ninguém remove
    assert not can_remove("", {"added_by": ""})


def test_ensure_can_remove_raises_not_owner():
    from karaoke.errors import ApiError

    with pytest.raises(ApiError) as exc:
        ensure_can_remove("guest:y", {"added_by": "guest:x"}, "q_2")
    assert exc.value.status == 403 and exc.value.code == "not_owner"


# ---- regra de posse via HTTP com dois atores ----------------------------------------------------
@pytest.fixture
def owned_client(app, monkeypatch):
    """App com uma rota de teste que usa a política única (como DELETE /api/queue/{id} usará)."""
    items = {"q_1": {"id": "q_1", "added_by": "host"}, "q_2": {"id": "q_2", "added_by": "guest:x"}}
    router = APIRouter(dependencies=[scope("guest-safe")])

    @router.delete("/api/_test/items/{item_id}", status_code=204)
    async def _remove(item_id: str, actor: str = Depends(current_actor)) -> None:
        ensure_can_remove(actor, items[item_id], item_id)
        del items[item_id]

    app.include_router(router)
    actor = {"p": HOST}
    monkeypatch.setattr(security, "resolve_principal", lambda request: actor["p"])
    with TestClient(app, base_url=BASE_URL, client=("127.0.0.1", 50000)) as c:
        yield c, actor, items


def test_http_owner_rule_two_actors(owned_client):
    c, actor, items = owned_client
    # guest:x tenta remover item do host → 403 not_owner
    actor["p"] = GUEST_X
    r = c.delete("/api/_test/items/q_1")
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_owner"
    # o host modera (DECISION 0006): remove o item do guest:x
    actor["p"] = HOST
    assert c.delete("/api/_test/items/q_2").status_code == 204
    assert c.delete("/api/_test/items/q_1").status_code == 204
    assert items == {}


def test_queue_added_by_comes_from_principal(client, app):
    # T-005: a rota exige uma música existente; cria a linha mínima antes.
    with app.state.db.tx() as conn:
        conn.execute(
            "INSERT INTO songs(id, title, status, added_by, created_at, updated_at) "
            "VALUES ('fJ9rUzIMcZQ', 'Bohemian Rhapsody', 'ready', 'host', '2026-10-04T00:00:00Z', '2026-10-04T00:00:00Z')"
        )
    r = client.post("/api/queue", json={"song_id": "fJ9rUzIMcZQ", "added_by": "guest:evil"})
    assert r.status_code == 201
    assert r.json()["added_by"] == "host"
