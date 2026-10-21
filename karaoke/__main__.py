"""`python -m karaoke` (= `make start`): config, ambiente dos modelos/CUDA, porta com fallback, uvicorn, navegador.

Encerramento (ARCHITECTURE §8, FIX-002):
- 1º Ctrl+C/SIGTERM: `app.state.bus.close_all()` roda no loop na hora, então os streams SSE (Palco/Controle) terminam
  e o uvicorn encerra as conexões; o que sobrar é cancelado após `GRACEFUL_TIMEOUT_S`. Depois vem o lifespan
  shutdown (runner mata o worker, player salva a posição).
- 2º sinal (qualquer um) ou `HARD_EXIT_AFTER_S` sem terminar: para o runner (mata o worker) e sai com `os._exit`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import logging.handlers
import os
import socket
import sys
import threading
import webbrowser
from types import FrameType
from typing import Any

from karaoke.config import PORT_FALLBACK_TRIES, Config, ConfigError, load_config

log = logging.getLogger("karaoke.main")

GRACEFUL_TIMEOUT_S = 3  # uvicorn: espera por conexões/tarefas antes de cancelar
HARD_EXIT_AFTER_S = 10.0  # watchdog a partir do 1º sinal
HARD_EXIT_STOP_S = 2.0  # quanto o encerramento forçado espera o runner matar o worker


def pick_port(host: str, start: int, tries: int = PORT_FALLBACK_TRIES) -> int:
    """Primeira porta livre em [start, start+tries].

    `SO_REUSEADDR` (POSIX) faz o teste agir como o bind do uvicorn/asyncio: sockets em TIME_WAIT de uma execução
    anterior não contam como "ocupada" (QA M-01), mas um servidor escutando na porta continua contando.
    """
    for port in range(start, start + tries + 1):
        with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM) as s:
            if os.name != "nt":  # no Windows SO_REUSEADDR permitiria "roubar" uma porta em uso
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    raise ConfigError(f"Nenhuma porta livre entre {start} e {start + tries}. Defina KARAOKE_PORT.")


def setup_logging(config: Config) -> None:
    config.logs_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        config.logs_dir / "karaoke.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger("karaoke").addHandler(fh)
    logging.getLogger("karaoke").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # URLs em INFO; a da API do Vagalume leva a chave


def prepare_environment(config: Config) -> None:
    """Herdado pelos subprocessos de IA (spawn): modelos em data/models, libs CUDA 12 do venv, deno no PATH."""
    from karaoke import system

    os.environ.update(system.model_env(config))
    os.environ.update({k: v for k, v in system.cuda_env().items() if k == "LD_LIBRARY_PATH"})
    os.environ["PATH"] = system.venv_bin_on_path()["PATH"]


def _stop_runner(app: Any, timeout: float) -> None:
    """Para o JobRunner (mata o subprocesso worker) sem esperar mais que `timeout`."""
    runner = getattr(getattr(app, "state", None), "runner", None)
    if runner is None:
        return
    t = threading.Thread(target=lambda: runner.stop(timeout=timeout), name="hard-exit-stop", daemon=True)
    t.start()
    t.join(timeout + 0.5)


def hard_exit(app: Any, code: int = 1) -> None:
    log.warning("encerramento forçado")
    with contextlib.suppress(Exception):
        _stop_runner(app, HARD_EXIT_STOP_S)
    with contextlib.suppress(Exception):
        sys.stdout.flush()
        sys.stderr.flush()
    os._exit(code)


def _make_server(uvicorn_config: Any, app: Any) -> Any:
    import uvicorn

    class KaraokeServer(uvicorn.Server):
        """uvicorn.Server que fecha os streams SSE no 1º sinal e força a saída no 2º (ou pelo watchdog)."""

        _loop: asyncio.AbstractEventLoop | None = None
        _watchdog: threading.Timer | None = None

        async def startup(self, sockets: list[socket.socket] | None = None) -> None:
            self._loop = asyncio.get_running_loop()
            await super().startup(sockets=sockets)

        def handle_exit(self, sig: int, frame: FrameType | None) -> None:
            first = not self.should_exit
            super().handle_exit(sig, frame)
            if not first:
                self.force_exit = True
                hard_exit(app, 128 + sig)
                return
            loop = self._loop
            if loop is not None and not loop.is_closed():
                loop.call_soon_threadsafe(self.begin_shutdown)
            self._watchdog = threading.Timer(HARD_EXIT_AFTER_S, hard_exit, args=(app, 128 + sig))
            self._watchdog.daemon = True
            self._watchdog.start()

        def begin_shutdown(self) -> None:
            bus = getattr(app.state, "bus", None)
            if bus is not None:
                bus.close_all()
            guest = getattr(app.state, "guest", None)
            if guest is not None:  # v0.3: o listener da LAN também para (ADR-0003 §1)
                guest.request_stop()

    return KaraokeServer(uvicorn_config)


def main() -> int:
    try:
        config = load_config()
        requested_port = config.port
        config = config.with_port(pick_port(config.host, config.port))
    except ConfigError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2
    config.ensure_dirs()
    setup_logging(config)
    prepare_environment(config)

    import uvicorn

    from karaoke.app import create_app

    url = f"http://127.0.0.1:{config.port}/"
    bar = "=" * 64
    # 1ª linha contém a URL: os testes de ciclo de vida a leem para descobrir a porta.
    print(
        f"Karaokê rodando em {url}\n"
        f"{bar}\n"
        f"  Controle (este PC):        {url}\n"
        f"  Palco (TV, tela cheia):    {url}stage\n"
        f"                             (no Palco, aperte Enter para liberar o som)\n\n"
        f"  Para sair: Ctrl+C\n"
        f"{bar}",
        flush=True,
    )
    if config.port != requested_port:
        print(f"  (porta {requested_port} estava ocupada; usando {config.port})\n", flush=True)
    if config.open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app = create_app(config)
    server = _make_server(
        uvicorn.Config(
            app,
            host=config.host,
            port=config.port,
            log_level="warning",
            proxy_headers=False,
            server_header=False,
            timeout_graceful_shutdown=GRACEFUL_TIMEOUT_S,
        ),
        app,
    )
    with contextlib.suppress(KeyboardInterrupt):
        server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
