"""LanListener real (ADR-0003 §1): 2º uvicorn no mesmo loop em 127.0.0.2, start/stop, sinais, stop com SSE aberto.

Portas 8850–8859 (exclusivas destes testes). O servidor principal roda em thread (127.0.0.1); para simular um
celular, os testes de convidado tratam conexões que chegam por 127.0.0.2 como "fora do loopback".
"""

from __future__ import annotations

import contextlib
import signal
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from karaoke import security
from karaoke.app import create_app
from karaoke.config import Config
from karaoke.errors import ApiError
from karaoke.guest.listener import LanServer, open_socket
from karaoke.guest.netinfo import LanAddress

LAN = "127.0.0.2"


def _free_port() -> int:
    for port in range(8850, 8860):
        ok = True
        for host in ("127.0.0.1", LAN):
            with socket.socket() as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s.bind((host, port))
                except OSError:
                    ok = False
        if ok:
            return port
    pytest.skip("nenhuma porta livre entre 8850 e 8859")


def _refused(host: str, port: int) -> bool:
    try:
        socket.create_connection((host, port), timeout=1).close()
    except ConnectionRefusedError:
        return True
    return False


def _wait(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def live(tmp_path):
    port = _free_port()
    config = Config(port=port, data_dir=tmp_path / "data", device="cpu", open_browser=False)
    app = create_app(config)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    assert _wait(lambda: server.started, 10)
    app.state.guest.discover = lambda: [LanAddress(LAN, "lo-test", "ethernet", True)]
    host = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5)
    try:
        yield app, host, port
    finally:
        with contextlib.suppress(Exception):
            host.post("/api/guest/disable")
        host.close()
        server.should_exit = True
        thread.join(10)


def test_lan_server_does_not_capture_signals():
    before = signal.getsignal(signal.SIGINT)
    server = LanServer(uvicorn.Config(app=None))
    with server.capture_signals():
        assert signal.getsignal(signal.SIGINT) is before


def test_bind_error_is_lan_bind_failed():
    port = _free_port()
    with socket.socket() as busy:
        busy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # porta pode estar em TIME_WAIT
        busy.bind((LAN, port))
        busy.listen(1)
        with pytest.raises(ApiError) as exc:
            open_socket(LAN, port)
    assert exc.value.code == "lan_bind_failed" and exc.value.details["reason"] == "EADDRINUSE"


def test_enable_disable_opens_and_closes_socket(live):
    app, host, port = live
    assert _refused(LAN, port), "modo desligado: nada escuta fora do 127.0.0.1"
    st = host.post("/api/guest/enable", json={"address": LAN}).json()
    assert st["enabled"] is True
    # loopback chegando pelo listener da LAN continua sendo host
    r = httpx.get(f"http://{LAN}:{port}/api/health", timeout=5)
    assert r.status_code == 200
    host.post("/api/guest/disable")
    assert _refused(LAN, port)
    # religar funciona (o socket foi realmente fechado)
    assert host.post("/api/guest/enable", json={"address": LAN}).status_code == 200
    assert not _refused(LAN, port)


def test_guest_flow_and_stop_with_open_sse(live, monkeypatch):
    app, host, port = live
    # conexões por 127.0.0.2 contam como celular (fora do loopback)
    monkeypatch.setattr(security, "_is_loopback_client", lambda r: r.scope["server"][0] != LAN)
    st = host.post("/api/guest/enable", json={"address": LAN}).json()
    token = st["url"].split("#t=")[1]
    base = f"http://{LAN}:{port}"
    with httpx.Client(base_url=base, timeout=5) as phone:
        assert phone.get("/api/health").status_code == 401  # anon
        r = phone.post("/api/guest/session", json={"token": token, "name": "Bia"}, headers={"Origin": base})
        assert r.status_code == 201
        assert phone.get("/api/queue").status_code == 200
        assert phone.get("/api/settings").status_code == 403

        lines: list[str] = []
        done = threading.Event()

        def read_sse():
            try:
                with phone.stream("GET", "/api/events?role=guest&client_id=c_phone", timeout=10) as s:
                    for line in s.iter_lines():
                        lines.append(line)
            except Exception:  # noqa: BLE001
                pass
            done.set()

        threading.Thread(target=read_sse, daemon=True).start()
        assert _wait(lambda: "event: hello" in lines)
        assert _wait(lambda: host.get("/api/guest/state").json()["connected"] == 1, 3)
        t0 = time.monotonic()
        assert host.post("/api/guest/disable").status_code == 200
        assert time.monotonic() - t0 < 4  # stop termina mesmo com SSE aberto
        assert done.wait(5)
        assert "event: guest.ended" in lines
        assert any('"reason":"disabled"' in line for line in lines)
    assert _refused(LAN, port)


def test_stop_with_open_host_sse_on_listener(live):
    """Stream que não é de convidado (não fechado por close_role) também não segura o stop."""
    app, host, port = live
    host.post("/api/guest/enable", json={"address": LAN})
    done = threading.Event()

    def read_sse():
        try:
            with httpx.stream("GET", f"http://{LAN}:{port}/api/events?role=control", timeout=10) as s:
                for _ in s.iter_lines():
                    pass
        except Exception:  # noqa: BLE001
            pass
        done.set()

    threading.Thread(target=read_sse, daemon=True).start()
    assert _wait(lambda: app.state.bus.count("control") == 1)
    t0 = time.monotonic()
    host.post("/api/guest/disable")
    assert time.monotonic() - t0 < 4.5
    assert done.wait(5) and _refused(LAN, port)


# ---- review-1 M-3: o listener da LAN limita conexões ------------------------------------------------------------
def _closed_by_server(sock: socket.socket, timeout: float) -> bool:
    sock.settimeout(timeout)
    try:
        return sock.recv(1) == b""
    except TimeoutError:
        return False
    except ConnectionResetError:
        return True


def test_lan_listener_limits_and_idle_connection_timeout(live, monkeypatch):
    from karaoke.guest import listener as lan_mod

    app, host, port = live
    monkeypatch.setattr(lan_mod, "HEADER_TIMEOUT_S", 0.3)
    host.post("/api/guest/enable", json={"address": LAN})
    cfg = app.state.guest.listener._server.config
    assert cfg.limit_concurrency == lan_mod.LAN_LIMIT_CONCURRENCY >= 30  # ≥ SSE_MAX_TOTAL
    assert cfg.timeout_keep_alive == lan_mod.LAN_KEEP_ALIVE_S and cfg.http is lan_mod.LanH11Protocol
    with socket.create_connection((LAN, port), timeout=2) as idle:  # slowloris: nunca manda a requisição
        idle.sendall(b"GET /api/health HTTP/1.1\r\n")
        assert _closed_by_server(idle, 3)
    r = httpx.get(f"http://{LAN}:{port}/api/health", timeout=5)  # requisição normal segue funcionando
    assert r.status_code == 200


def test_lan_listener_caps_connections(live, monkeypatch):
    from karaoke.guest import listener as lan_mod

    app, host, port = live
    monkeypatch.setattr(lan_mod, "LAN_MAX_CONNECTIONS", 2)
    host.post("/api/guest/enable", json={"address": LAN})
    socks = [socket.create_connection((LAN, port), timeout=2) for _ in range(2)]
    try:
        time.sleep(0.2)
        with socket.create_connection((LAN, port), timeout=2) as extra:
            assert _closed_by_server(extra, 2)  # acima do teto: fechada logo após o accept
        assert not _closed_by_server(socks[0], 0.2)  # as anteriores continuam abertas
    finally:
        for s in socks:
            s.close()
