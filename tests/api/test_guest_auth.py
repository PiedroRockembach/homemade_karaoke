"""`authorize` v0.3 (ADR-0003 §4): escopos host/guest-safe/public, papéis host/guest/anon, Host/Origin dinâmicos."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from karaoke.security import declared_scope, iter_api_routes
from tests.api.guest_helpers import LAN_BASE, LAN_IP, WIFI_IP
from tests.conftest import BASE_URL, TEST_PORT


def code(r) -> str:
    return r.json()["error"]["code"]


def test_mode_off_lan_request_forbidden(guest_env):
    lan = guest_env.lan()
    r = lan.get("/api/health")
    assert r.status_code == 403  # Host do IP da LAN ainda não é permitido
    # mesmo com Host permitido, cliente fora do loopback com o modo desligado → guest_mode_off
    c = guest_env.lan()
    r = c.get("/api/guest/session", headers={"Host": f"127.0.0.1:{TEST_PORT}"})
    assert r.status_code == 403 and r.json()["error"]["details"]["reason"] == "guest_mode_off"


def test_lan_host_and_origin_only_while_enabled(guest_env):
    host = guest_env.host
    lan_host = {"Host": f"{LAN_IP}:{TEST_PORT}"}
    assert host.get("/api/health", headers=lan_host).status_code == 403
    guest_env.enable()
    assert host.get("/api/health", headers=lan_host).status_code == 200
    # Origin da LAN aceito em mutação; outra origem não
    assert host.post("/api/guest/rotate", headers={"Origin": LAN_BASE}).status_code == 200
    r = host.post("/api/guest/rotate", headers={"Origin": f"http://{WIFI_IP}:{TEST_PORT}"})
    assert r.status_code == 403 and code(r) == "forbidden"
    host.post("/api/guest/disable")
    assert host.get("/api/health", headers=lan_host).status_code == 403


def test_connection_outside_lan_listener_is_refused(guest_env):
    guest_env.enable(LAN_IP)
    # cliente não-loopback chegando por outro "servidor" (o principal, em 127.0.0.1) → guest_mode_off
    c = TestClient(guest_env.app, base_url=BASE_URL, client=("192.168.1.50", 1))
    c.portal = guest_env.host.portal
    r = c.get("/api/health")
    assert r.status_code == 403 and r.json()["error"]["details"]["reason"] == "guest_mode_off"


def test_anon_reaches_only_public(guest_env):
    guest_env.enable()
    anon = guest_env.lan()
    r = anon.get("/api/guest/session")
    assert r.status_code == 401 and code(r) == "guest_session_required"
    r = anon.get("/api/queue")  # guest-safe
    assert r.status_code == 401 and code(r) == "guest_session_required"
    r = anon.get("/api/settings")  # host
    assert r.status_code == 403 and r.json()["error"]["details"]["reason"] == "host_only"
    assert anon.get("/static/js/shared/api.js").status_code == 200  # public
    assert anon.get("/static/../karaoke/app.py").status_code in (403, 404)


def test_guest_page_404_until_created(guest_env):
    from karaoke.config import WEB_DIR

    guest_env.enable()
    r = guest_env.lan().get("/guest")
    assert r.status_code == (200 if (WEB_DIR / "guest.html").is_file() else 404)


def _host_routes(app):
    for path, route in iter_api_routes(app):
        if declared_scope(route) == "host" or declared_scope(route) is None:
            for m in route.methods:
                yield m, path


def test_guest_gets_403_on_every_host_route(guest_env):
    """Deny-by-default: toda rota `host` (inclusive sem marcador) recusa o convidado com 403 host_only."""
    guest_env.enable()
    guest = guest_env.join()
    guest_env.app.state.rate_limiter = None  # o teste varre muitas rotas
    checked = 0
    for method, path in _host_routes(guest_env.app):
        url = path.replace("{song_id}", "fJ9rUzIMcZQ").replace("{item_id}", "q_1").replace("{job_id}", "j_1")
        url = url.replace("{model_id}", "htdemucs").replace("{path:path}", "x")
        r = guest.request(method, url, json={} if method in ("POST", "PUT", "PATCH") else None)
        assert r.status_code == 403, (method, url, r.status_code)
        assert r.json()["error"]["details"]["reason"] == "host_only", (method, url)
        checked += 1
    assert checked >= 25


def test_every_route_declares_scope_including_public(app):
    scopes = {declared_scope(r) for _, r in iter_api_routes(app)}
    assert {"host", "guest-safe", "public"} <= scopes


@pytest.mark.parametrize("path", ["/api/health", "/api/queue", "/api/player", "/api/songs"])
def test_guest_reaches_guest_safe(guest_env, path):
    guest_env.enable()
    assert guest_env.join().get(path).status_code == 200


def test_x_forwarded_for_does_not_spoof_loopback(guest_env):
    guest_env.enable()
    r = guest_env.lan().get("/api/settings", headers={"X-Forwarded-For": "127.0.0.1"})
    assert r.status_code == 403


def test_guest_rate_limited_with_retry_after(guest_env):
    guest_env.enable()
    guest = guest_env.join()
    codes = [guest.post("/api/queue", json={"song_id": "fJ9rUzIMcZQ"}).status_code for _ in range(11)]
    assert codes[-1] == 429
    r = guest.post("/api/queue", json={"song_id": "fJ9rUzIMcZQ"})
    assert r.status_code == 429 and code(r) == "rate_limited"
    assert r.headers["Retry-After"] == str(r.json()["error"]["details"]["retry_after_s"])
    assert r.json()["error"]["details"]["reason"] == "write"
    # o host nunca é limitado
    for _ in range(15):
        assert guest_env.host.get("/api/search?q=").status_code != 429
