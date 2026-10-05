"""`karaoke.__main__`: porta (QA M-01) e encerramento forçado (QA H-01). Dono: architect."""

from __future__ import annotations

import signal
import socket
import time
from types import SimpleNamespace

import pytest

from karaoke import __main__ as main_mod

PORTS = range(8860, 8870)


def _free_port() -> int:
    for port in PORTS:
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    pytest.skip("nenhuma porta livre em 8860–8869")


def _leave_time_wait(port: int) -> None:
    """Servidor fecha primeiro uma conexão aceita → o lado do servidor (a porta) fica em TIME_WAIT."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen()
    cli = socket.create_connection(("127.0.0.1", port))
    conn, _ = srv.accept()
    conn.close()  # servidor fecha primeiro (como no Ctrl+C com Palco aberto)
    srv.close()
    time.sleep(0.05)
    cli.close()


def test_pick_port_reuses_port_in_time_wait() -> None:
    port = _free_port()
    _leave_time_wait(port)
    # sem SO_REUSEADDR o bind falharia (o bug do M-01)
    with socket.socket() as probe, pytest.raises(OSError):
        probe.bind(("127.0.0.1", port))
    assert main_mod.pick_port("127.0.0.1", port, tries=3) == port


def test_pick_port_skips_port_with_listener() -> None:
    port = _free_port()
    with socket.socket() as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen()
        assert main_mod.pick_port("127.0.0.1", port, tries=3) != port


class _FakeRunner:
    def __init__(self) -> None:
        self.stopped_with: float | None = None

    def stop(self, timeout: float = 10.0) -> None:
        self.stopped_with = timeout


def _fake_app(runner: object | None = None) -> SimpleNamespace:
    closed: list[bool] = []
    bus = SimpleNamespace(close_all=lambda: closed.append(True))
    return SimpleNamespace(state=SimpleNamespace(runner=runner, bus=bus), closed=closed)


def test_hard_exit_stops_runner_then_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _FakeRunner()
    codes: list[int] = []
    monkeypatch.setattr(main_mod.os, "_exit", codes.append)
    main_mod.hard_exit(_fake_app(runner), 130)
    assert runner.stopped_with == main_mod.HARD_EXIT_STOP_S
    assert codes == [130]


def test_first_signal_closes_streams_second_forces_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    app = _fake_app(_FakeRunner())
    calls: list[int] = []
    monkeypatch.setattr(main_mod, "hard_exit", lambda _app, code=1: calls.append(code))
    server = main_mod._make_server(uvicorn.Config(app, timeout_graceful_shutdown=3), app)

    class _Loop:
        def is_closed(self) -> bool:
            return False

        def call_soon_threadsafe(self, fn, *args):  # noqa: ANN001, ANN002, ANN202
            fn(*args)

    server._loop = _Loop()
    server.handle_exit(signal.SIGTERM, None)
    assert server.should_exit and not server.force_exit
    assert app.closed == [True]  # streams SSE fechados já no 1º sinal
    assert calls == []
    server._watchdog.cancel()

    server.handle_exit(signal.SIGTERM, None)  # 2º sinal, mesmo SIGTERM, força a saída
    assert server.force_exit
    assert calls == [128 + signal.SIGTERM]
