"""E2E: Ctrl+C com Palco e Controle abertos no Chrome encerra rápido e o reinício volta na mesma porta,
com o Palco reconectando sozinho (QA H-01 / M-01). Dono: architect (FIX-002). Portas 8860–8869.
"""

from __future__ import annotations

import signal
import socket
import subprocess
import time
from pathlib import Path

import pytest

from .conftest import start_server, stop_server

pytestmark = pytest.mark.e2e

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


def _stages(live) -> int:  # noqa: ANN001
    return live.get("/api/player")["stage"]["connected"]


def test_ctrl_c_with_stage_open_exits_and_restart_keeps_port(tmp_path: Path, browser) -> None:  # noqa: ANN001
    data = tmp_path / "data"
    live = start_server(data, _free_port())
    port = int(live.base.rsplit(":", 1)[1])
    ctx_stage = browser.new_context(viewport={"width": 1280, "height": 720})
    ctx_ctrl = browser.new_context(viewport={"width": 1280, "height": 800})
    try:
        stage = ctx_stage.new_page()
        stage.goto(f"{live.base}/stage")
        control = ctx_ctrl.new_page()
        control.goto(f"{live.base}/")
        control.wait_for_selector("#tab-search")
        live.wait(lambda: _stages(live) >= 1, msg="Palco conectado por SSE")
        time.sleep(0.3)

        t0 = time.monotonic()
        live.proc.send_signal(signal.SIGINT)
        try:
            live.proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            pytest.fail("Ctrl+C não encerrou o app com Palco/Controle abertos")
        elapsed = time.monotonic() - t0
        assert elapsed <= 3.0, f"encerrou em {elapsed:.2f}s"
        live.http.close()

        live2 = start_server(data, port)
        try:
            # start_server só volta quando /api/health responde *nesta* porta: se o app tivesse caído no
            # fallback (8861…), nada responderia aqui e o start falharia.
            # o EventSource do Palco reconecta sozinho ao mesmo endereço (sem recarregar a página)
            live2.wait(lambda: _stages(live2) >= 1, timeout=10, msg="Palco reconectado")
        finally:
            stop_server(live2)
    finally:
        ctx_stage.close()
        ctx_ctrl.close()
        if live.proc.poll() is None:
            stop_server(live)
