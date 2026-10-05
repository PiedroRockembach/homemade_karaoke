"""Processo real (`python -m karaoke`): encerra com streams SSE abertos (QA H-01) e reusa a porta (QA M-01).

Dono: architect. Portas 8860–8869, data dir temporário, sem navegador.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PORTS = range(8860, 8870)
EXIT_BUDGET_S = 3.0


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


def _start(data_dir: Path, port: int) -> tuple[subprocess.Popen, int]:
    env = dict(os.environ)
    env.update(
        KARAOKE_PORT=str(port), KARAOKE_DATA_DIR=str(data_dir), KARAOKE_NO_BROWSER="1", KARAOKE_DEVICE="cpu"
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "karaoke"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    line = proc.stdout.readline() if proc.stdout else ""
    if "127.0.0.1:" not in line:
        proc.kill()
        pytest.fail(f"servidor não subiu: {line!r}")
    real = int(line.split("127.0.0.1:")[1].split("/")[0])
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", real), timeout=0.5):
            return proc, real
        time.sleep(0.1)
    proc.kill()
    pytest.fail("servidor não aceitou conexões em 20 s")


def _open_sse(port: int, role: str) -> socket.socket:
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(
        f"GET /api/events?role={role}&client_id=c_{role} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\nAccept: text/event-stream\r\n\r\n".encode()
    )
    data = b""
    while b"event: hello" not in data:
        chunk = s.recv(4096)
        assert chunk, "stream fechou antes do hello"
        data += chunk
    assert data.startswith(b"HTTP/1.1 200")
    return s


def _stop(proc: subprocess.Popen, sig: int) -> float:
    t0 = time.monotonic()
    proc.send_signal(sig)
    try:
        proc.wait(timeout=EXIT_BUDGET_S + 2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        pytest.fail(f"processo não encerrou após {signal.Signals(sig).name} com streams SSE abertos")
    return time.monotonic() - t0


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM], ids=["SIGINT", "SIGTERM"])
def test_exits_fast_with_stage_and_control_connected(tmp_path: Path, sig: int) -> None:
    proc, port = _start(tmp_path / "data", _free_port())
    socks = [_open_sse(port, "stage"), _open_sse(port, "control")]
    try:
        elapsed = _stop(proc, sig)
    finally:
        for s in socks:
            s.close()
    assert elapsed <= EXIT_BUDGET_S, f"encerrou em {elapsed:.2f}s"


def test_restart_reuses_same_port(tmp_path: Path) -> None:
    data = tmp_path / "data"
    proc, port = _start(data, _free_port())
    socks = [_open_sse(port, "stage"), _open_sse(port, "control")]
    _stop(proc, signal.SIGINT)
    for s in socks:
        s.close()
    proc2, port2 = _start(data, port)
    try:
        assert port2 == port, f"reinício trocou a porta {port} → {port2}"
    finally:
        _stop(proc2, signal.SIGINT)
