"""GuestService + rotas §12 (ADR-0003 §4, §7): convite, sessões, enable/disable/rotate/end-sessions, eventos."""

from __future__ import annotations

import asyncio

import pytest

from karaoke.api.events import hello_snapshot
from karaoke.errors import lan_bind_failed
from karaoke.events import Event, Subscription, visible_to
from karaoke.guest import service as guest_service
from karaoke.security import HOST, Principal
from tests.api.guest_helpers import LAN_IP, WIFI_IP
from tests.conftest import TEST_PORT


def code(r) -> str:
    return r.json()["error"]["code"]


@pytest.fixture
def events(guest_env):
    got: list[tuple[str, object]] = []
    guest_env.app.state.bus.add_listener(lambda e: got.append((e.type, e.data)))
    return got


def run(env, coro_fn):
    return env.host.portal.call(coro_fn)


# ---- estado / enable / disable -------------------------------------------------------------------------
def test_starts_disabled(guest_env):
    st = guest_env.host.get("/api/guest/state").json()
    assert st["enabled"] is False and st["url"] is None and st["qr"] is None and st["invite_version"] == 0
    assert st["port"] == TEST_PORT and [a["address"] for a in st["addresses"]] == [LAN_IP, WIFI_IP]
    assert guest_env.listener.calls == []  # nenhum socket fora do loopback


def test_enable_defaults_to_recommended(guest_env, events):
    st = guest_env.enable()
    assert st["enabled"] is True and st["address"] == LAN_IP
    assert st["url"].startswith(f"http://{LAN_IP}:{TEST_PORT}/guest#t=")
    assert len(st["url"].split("#t=")[1]) == 22
    assert st["display_url"] == f"{LAN_IP}:{TEST_PORT}/guest"
    assert st["qr"] == f"/api/guest/qr?v={st['invite_version']}" and st["invite_expires_at"].endswith("Z")
    assert guest_env.listener.calls == [("start", LAN_IP, TEST_PORT)]
    assert any(t == "guest.state" and d["enabled"] and d["url"] == st["url"] for t, d in events)
    # idempotente
    assert guest_env.enable()["invite_version"] == st["invite_version"]


def test_enable_errors(guest_env):
    host = guest_env.host
    r = host.post("/api/guest/enable", json={"address": "10.9.9.9"})
    assert r.status_code == 400 and code(r) == "invalid_request"
    guest_env.listener.fail = lan_bind_failed(LAN_IP, TEST_PORT, "EADDRINUSE")
    r = host.post("/api/guest/enable", json={"address": LAN_IP})
    assert r.status_code == 409 and code(r) == "lan_bind_failed"
    assert r.json()["error"]["details"] == {"address": LAN_IP, "port": TEST_PORT, "reason": "EADDRINUSE"}
    st = host.get("/api/guest/state").json()
    assert st["enabled"] is False and st["error"]["code"] == "lan_bind_failed"
    guest_env.service.discover = lambda: []
    r = host.post("/api/guest/enable")
    assert r.status_code == 409 and code(r) == "lan_unavailable"


def test_switch_address_reopens_listener_and_drops_sessions(guest_env):
    guest_env.enable(LAN_IP)
    guest_env.join()
    st = guest_env.enable(WIFI_IP)
    assert st["address"] == WIFI_IP and st["sessions"] == 0
    assert guest_env.listener.calls[-2:] == [("stop",), ("start", WIFI_IP, TEST_PORT)]


def test_disable_ends_everything(guest_env, events):
    guest_env.enable()
    guest = guest_env.join()
    st = guest_env.host.post("/api/guest/disable").json()
    assert st["enabled"] is False and st["sessions"] == 0 and st["url"] is None
    assert guest_env.listener.calls[-1] == ("stop",)
    assert guest_env.service.invite is None
    # (na vida real o listener sumiu: conexão recusada) — requisição que ainda chegue é recusada
    assert guest.get("/api/queue").status_code == 403
    assert guest_env.host.post("/api/guest/disable").status_code == 200  # idempotente
    r = guest_env.host.post("/api/guest/rotate")
    assert r.status_code == 409 and code(r) == "guest_mode_off"
    assert code(guest_env.host.post("/api/guest/end-sessions")) == "guest_mode_off"
    assert code(guest_env.host.get("/api/guest/qr")) == "guest_mode_off"


# ---- sessão ----------------------------------------------------------------------------------------------
def test_session_cookie_and_me(guest_env):
    guest_env.enable()
    lan = guest_env.lan()
    r = lan.post("/api/guest/session", json={"token": guest_env.token(), "name": "  Bia‮  \x07 Souza "})
    assert r.status_code == 201
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("karaoke_guest=") and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert "Path=/" in cookie and "Max-Age=43200" in cookie and "Secure" not in cookie
    assert len(cookie.split(";")[0].split("=", 1)[1]) >= 43
    me = r.json()["guest"]
    assert me["id"].startswith("g_") and me["name"] == "Bia Souza"
    assert me["limits"] == {
        "max_duration_ms": 900000,
        "search_per_min": 30,
    }
    assert lan.get("/api/guest/session").json()["guest"]["id"] == me["id"]
    assert guest_env.host.get("/api/guest/state").json()["sessions"] == 1


def test_session_ids_are_persistent_counter(guest_env):
    guest_env.enable()
    a = guest_env.join("A").get("/api/guest/session").json()["guest"]["id"]
    b = guest_env.join("B", ip="192.168.1.51").get("/api/guest/session").json()["guest"]["id"]
    assert int(b[2:]) == int(a[2:]) + 1
    with guest_env.app.state.db.tx() as conn:
        assert conn.execute("SELECT value FROM counters WHERE name='guest'").fetchone()[0] == int(b[2:])


@pytest.mark.parametrize(
    ("body", "status", "err"),
    [
        ({"token": "x" * 22, "name": "Bia"}, 401, "guest_invite_invalid"),
        ({"name": "Bia"}, 400, "invalid_request"),
        ({"token": "TOKEN", "name": "   "}, 400, "invalid_request"),
        ({"token": "TOKEN", "name": "x" * 41}, 400, "invalid_request"),
    ],
)
def test_session_errors(guest_env, body, status, err):
    guest_env.enable()
    if body.get("token") == "TOKEN":
        body = {**body, "token": guest_env.token()}
    r = guest_env.lan().post("/api/guest/session", json=body)
    assert r.status_code == status and code(r) == err


def test_host_cannot_create_guest_session(guest_env):
    guest_env.enable()
    r = guest_env.host.post("/api/guest/session", json={"token": guest_env.token(), "name": "Eu"})
    assert r.status_code == 403 and code(r) == "forbidden"


def test_rotate_invalidates_token_but_keeps_sessions(guest_env):
    st = guest_env.enable()
    old = guest_env.token()
    guest = guest_env.join()
    st2 = guest_env.host.post("/api/guest/rotate").json()
    assert st2["invite_version"] == st["invite_version"] + 1 and st2["qr"] != st["qr"]
    assert guest.get("/api/queue").status_code == 200  # sessão continua
    r = guest_env.lan("192.168.1.70").post("/api/guest/session", json={"token": old, "name": "Caio"})
    assert r.status_code == 401 and code(r) == "guest_invite_invalid"


def test_end_sessions_401_and_rotates(guest_env, events):
    st = guest_env.enable()
    old = guest_env.token()
    guest = guest_env.join()
    st2 = guest_env.host.post("/api/guest/end-sessions").json()
    assert (
        st2["sessions"] == 0 and st2["enabled"] is True and st2["invite_version"] == st["invite_version"] + 1
    )
    r = guest.get("/api/queue")
    assert r.status_code == 401 and code(r) == "guest_session_required"
    r = guest_env.lan("192.168.1.71").post("/api/guest/session", json={"token": old, "name": "Bia"})
    assert code(r) == "guest_invite_invalid"
    assert any(t == "guest.state" and d["sessions"] == 0 for t, d in events)


def test_logout(guest_env):
    guest_env.enable()
    guest = guest_env.join()
    r = guest.delete("/api/guest/session")
    assert r.status_code == 204 and "Max-Age=0" in r.headers["set-cookie"]
    assert guest.get("/api/queue").status_code == 401


def test_new_session_replaces_old(guest_env):
    guest_env.enable()
    guest = guest_env.join("Bia")
    first = guest.get("/api/guest/session").json()["guest"]["id"]
    r = guest.post("/api/guest/session", json={"token": guest_env.token(), "name": "Bia 2"})
    assert r.status_code == 201 and r.json()["guest"]["id"] != first
    assert guest_env.service.session_count == 1


def test_too_many_guests(guest_env, monkeypatch):
    monkeypatch.setattr(guest_service, "MAX_SESSIONS", 2)
    guest_env.enable()
    guest_env.join("A", ip="192.168.1.80")
    guest_env.join("B", ip="192.168.1.81")
    r = guest_env.lan("192.168.1.82").post(
        "/api/guest/session", json={"token": guest_env.token(), "name": "C"}
    )
    assert r.status_code == 429 and r.json()["error"]["details"]["reason"] == "too_many_guests"


def test_session_bucket_limits_brute_force(guest_env):
    guest_env.enable()
    lan = guest_env.lan("192.168.1.90")
    codes = [
        lan.post("/api/guest/session", json={"token": "y" * 22, "name": "x"}).status_code for _ in range(11)
    ]
    assert codes == [401] * 10 + [429]  # balde `session`: capacidade 10 (DECISION 0016)


def test_qr_svg(guest_env):
    guest_env.enable()
    r = guest_env.host.get("/api/guest/qr?v=1")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert r.headers["cache-control"] == "no-store"
    assert r.text.startswith("<svg") and "viewBox" in r.text and "#000" in r.text
    assert guest_env.lan().get("/api/guest/qr").status_code == 403  # nunca para quem não é host


# ---- timer: rotação automática e expiração ------------------------------------------------------------
def test_tick_rotates_expired_invite_and_expires_sessions(guest_env):
    svc = guest_env.service
    now = [1_000_000.0]
    svc.clock = lambda: now[0]
    guest_env.enable()
    v = svc.invite.version
    guest = guest_env.join()
    now[0] += guest_service.INVITE_TTL_S + 1
    run(guest_env, svc.tick)
    assert svc.invite.version == v + 1 and svc.session_count == 0
    assert guest.get("/api/queue").status_code == 401


# ---- SSE de convidado ----------------------------------------------------------------------------------
def test_sse_role_rules_and_limits(guest_env, monkeypatch):
    guest_env.enable()
    guest = guest_env.join()
    r = guest.get("/api/events?role=control")
    assert r.status_code == 403 and code(r) == "forbidden"
    monkeypatch.setattr(guest_service, "SSE_MAX_PER_GUEST", 0)
    r = guest.get("/api/events?role=guest")
    assert r.status_code == 429 and r.json()["error"]["details"]["reason"] == "sse_limit"


def test_guest_hello_snapshot(guest_env):
    guest_env.enable()
    guest = guest_env.join()
    me = guest.get("/api/guest/session").json()["guest"]
    p = Principal(role="guest", id=me["id"], name=me["name"])
    hello = hello_snapshot(guest_env.app, "c_x", p.id, p)
    assert set(hello) == {"server_time", "client_id", "guest", "player", "queue", "jobs"}
    assert hello["guest"]["id"] == me["id"]
    host_hello = hello_snapshot(guest_env.app, "c_h", "host", HOST)
    assert host_hello["guest_state"]["enabled"] is True


def test_visible_to_filter():
    g = Subscription("c_g", "guest", Principal(role="guest", id="g_1", name="B"))
    h = Subscription("c_h", "control", HOST)

    def ev(t, data=None):
        return Event(1, t, data or {})

    for t in ("player.state", "queue.updated", "library.updated", "song.updated", "guest.ended"):
        assert visible_to(g, ev(t))
    for t in (
        "player.position",
        "settings.updated",
        "system.updated",
        "toast",
        "lyrics.updated",
        "guest.state",
    ):
        assert not visible_to(g, ev(t)) and visible_to(h, ev(t))
    assert visible_to(g, ev("job.updated"), "g_1") and not visible_to(g, ev("job.updated"), "g_2")
    assert not visible_to(g, ev("job.updated"), None)
    assert not visible_to(h, ev("guest.ended"))


def test_bus_job_index_and_close_role():
    from karaoke.events import EventBus

    async def scenario():
        bus = EventBus()
        bus.bind_loop()
        g1 = bus.subscribe("c_1", "guest", Principal(role="guest", id="g_1"))
        g2 = bus.subscribe("c_2", "guest", Principal(role="guest", id="g_2"))
        h = bus.subscribe("c_h", "control", HOST)
        bus.publish("job.updated", {"id": "j_5", "requested_by": "g_1", "stage": "queued"})
        bus.publish("job.removed", {"id": "j_5"})
        bus.publish("guest.state", {"enabled": True})
        assert bus.count_principal("g_1", "guest") == 1 and bus.principals("guest") == {"g_1", "g_2"}
        assert (
            bus.close_role(
                "guest", final=("guest.ended", {"reason": "ended"}), where=lambda s: s.principal.id == "g_1"
            )
            == 1
        )

        def drain(sub):
            out = []
            while not sub.queue.empty():
                e = sub.queue.get_nowait()
                out.append(None if e is None else e.type)
            return out

        assert drain(g1) == ["job.updated", "job.removed", "guest.ended", None]
        assert drain(g2) == []
        assert drain(h) == ["job.updated", "job.removed", "guest.state"]
        assert bus.count("guest") == 1

    asyncio.run(scenario())
