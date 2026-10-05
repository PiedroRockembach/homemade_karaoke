"""2º listener HTTP no IP da LAN, no mesmo processo e loop, com o mesmo app (ADR-0003 §1).

- O socket é criado por nós (`bind` síncrono): erro de porta/endereço vira `409 lan_bind_failed` na hora.
- `uvicorn.Config` com `lifespan="off"` (o lifespan já rodou no servidor principal), `proxy_headers=False` (um
  `X-Forwarded-For: 127.0.0.1` vindo da LAN não pode se passar por loopback), sem reconfigurar o logging.
- `LanServer` não captura sinais: o SIGINT/SIGTERM continuam com o `KaraokeServer` (servidor principal).
- `stop()` termina mesmo com streams SSE abertos: `timeout_graceful_shutdown=1` e, no pior caso, `force_exit` +
  `cancel` após `STOP_TIMEOUT_S`.
- Conexões (review-1 M-3): `limit_concurrency=LAN_LIMIT_CONCURRENCY` (30 SSE de convidado + folga para as requisições
  → `503` acima disso), `timeout_keep_alive` curto, `backlog` pequeno e o protocolo `LanH11Protocol`, que fecha na hora
  conexões acima de `LAN_MAX_CONNECTIONS` e as que não mandam a 1ª requisição completa em `HEADER_TIMEOUT_S`
  (slowloris). Os descritores são do mesmo processo do servidor principal: a LAN nunca pode esgotá-los.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import socket
from collections.abc import Iterator
from typing import Any

import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol

from karaoke.errors import lan_bind_failed

log = logging.getLogger("karaoke")

START_TIMEOUT_S = 2.0
STOP_TIMEOUT_S = 3.0
LAN_LIMIT_CONCURRENCY = 64  # ≥ 30 SSE de convidado (SSE_MAX_TOTAL) + requisições simultâneas
LAN_MAX_CONNECTIONS = 96  # acima disso a conexão é fechada logo após o accept (libera o descritor)
LAN_BACKLOG = 64
LAN_KEEP_ALIVE_S = 3
HEADER_TIMEOUT_S = 10.0


class LanH11Protocol(H11Protocol):
    """h11 com teto de conexões e prazo para a 1ª requisição chegar completa (o h11 do uvicorn não tem)."""

    _header_timer: asyncio.TimerHandle | None = None

    def connection_made(self, transport: asyncio.Transport) -> None:  # type: ignore[override]
        super().connection_made(transport)
        if len(self.connections) > LAN_MAX_CONNECTIONS:
            log.warning("modo convidado: conexões demais na LAN (%d); recusando", len(self.connections))
            transport.close()
            return
        self._header_timer = self.loop.call_later(HEADER_TIMEOUT_S, self._header_timeout)

    def _header_timeout(self) -> None:
        self._header_timer = None
        if self.cycle is None and not self.transport.is_closing():
            self.transport.close()  # nenhuma requisição completa até agora

    def connection_lost(self, exc: Exception | None) -> None:
        if self._header_timer is not None:
            self._header_timer.cancel()
            self._header_timer = None
        super().connection_lost(exc)


class LanServer(uvicorn.Server):
    """`uvicorn.Server` que não toma os handlers de sinal do servidor principal."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def open_socket(address: str, port: int) -> socket.socket:
    """Socket TCP já escutando em `address:port`. Falha → `ApiError 409 lan_bind_failed`."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((address, port))
        sock.listen(LAN_BACKLOG)
        sock.setblocking(False)
    except OSError as exc:
        sock.close()
        reason = errno.errorcode.get(exc.errno or 0, type(exc).__name__)
        log.warning("modo convidado: não foi possível escutar em %s:%s (%s)", address, port, reason)
        raise lan_bind_failed(address, port, reason) from exc
    return sock


class LanListener:
    def __init__(self, app: Any):
        self.app = app
        self.address: str | None = None
        self.port: int | None = None
        self._server: LanServer | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, address: str, port: int) -> None:
        if self.running:
            await self.stop()
        sock = open_socket(address, port)
        config = uvicorn.Config(
            self.app,
            host=address,
            port=port,
            lifespan="off",
            proxy_headers=False,
            server_header=False,
            date_header=True,
            log_config=None,
            log_level="warning",
            timeout_graceful_shutdown=1,
            http=LanH11Protocol,
            limit_concurrency=LAN_LIMIT_CONCURRENCY,
            timeout_keep_alive=LAN_KEEP_ALIVE_S,
            backlog=LAN_BACKLOG,
        )
        server = LanServer(config)
        task = asyncio.create_task(server.serve(sockets=[sock]), name=f"lan-listener {address}:{port}")
        self._server, self._task = server, task
        self.address, self.port = address, port
        loop = asyncio.get_running_loop()
        deadline = loop.time() + START_TIMEOUT_S
        while not server.started and not task.done() and loop.time() < deadline:
            await asyncio.sleep(0.01)
        if not server.started:
            await self.stop()
            sock.close()
            raise lan_bind_failed(address, port, "start_timeout")
        log.info("modo convidado: escutando em http://%s:%s", address, port)

    def request_stop(self) -> None:
        """Síncrono, sem lock (pode ser chamado no encerramento do app): só pede para o servidor parar."""
        if self._server is not None:
            self._server.should_exit = True

    async def stop(self) -> None:
        server, task = self._server, self._task
        self._server = self._task = None
        self.address = self.port = None
        if server is None or task is None:
            return
        server.should_exit = True
        if not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), STOP_TIMEOUT_S)
            except TimeoutError:
                log.warning("modo convidado: listener não parou em %.0f s; forçando", STOP_TIMEOUT_S)
                server.force_exit = True
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            except Exception:  # noqa: BLE001
                log.exception("erro ao parar o listener da LAN")
        elif not task.cancelled() and task.exception() is not None:
            log.warning("listener da LAN terminou com erro: %r", task.exception())
