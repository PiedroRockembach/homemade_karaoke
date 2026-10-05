"""EventBus para SSE (ARCHITECTURE §2, contrato §10).

- `publish(type, data)` pode ser chamado de qualquer thread (o JobRunner roda em thread): usa
  `loop.call_soon_threadsafe` quando fora do loop do servidor.
- Cada conexão SSE tem uma `Subscription` com sua própria `asyncio.Queue`. Assinante lento (fila cheia) é
  desconectado (o EventSource reconecta e recebe novo `hello`).
- Filtro por principal (futuro modo convidado): `visible_to(subscription, event)` — na v1 sempre True.
- `can_remove` por conexão (contrato §0.2): na entrega, `personalize(data, actor)` recalcula `can_remove` em todo
  objeto que tenha `added_by` + `can_remove` (Song, QueueItem) para o ator da assinatura. Quem publica pode montar o
  payload para qualquer ator.
- Encerramento: `shutdown()` (thread-safe, idempotente, sem locks — pode ser chamado de um handler de sinal) faz
  todos os streams SSE abertos terminarem na hora e recusa novas assinaturas. Precisa ser chamado **antes** de o
  servidor esperar as conexões fecharem (o uvicorn só roda o shutdown do lifespan depois disso). O `__main__` agenda
  `close_all()` no loop já no 1º sinal (mesmo efeito, chamado na thread do loop); o lifespan também o chama.
- Ouvintes internos: `add_listener(fn)` recebe cada `Event` publicado (na thread de quem publicou), para serviços
  reagirem a eventos de outros módulos (ex.: a fila reage a `job.updated`/`library.updated` do JobRunner).
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import threading
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from karaoke.security import HOST, Principal, can_remove

log = logging.getLogger("karaoke")

KEEPALIVE_S = 15.0
RETRY_MS = 1000
QUEUE_MAX = 1000
ROLES = ("control", "stage")


@dataclass(frozen=True)
class Event:
    id: int
    type: str
    data: Any

    def encode(self) -> str:
        payload = json.dumps(self.data, ensure_ascii=False, separators=(",", ":"))
        return f"id: {self.id}\nevent: {self.type}\ndata: {payload}\n\n"


@dataclass(eq=False)
class Subscription:
    client_id: str
    role: str
    principal: Principal = HOST
    queue: asyncio.Queue[Event | None] = field(default_factory=lambda: asyncio.Queue(QUEUE_MAX))
    closed: bool = False


def personalize(data: Any, actor: str) -> Any:
    """Cópia de `data` com `can_remove` recalculado para `actor` em cada dict que tem `added_by` e `can_remove`."""
    if isinstance(data, dict):
        out = {k: personalize(v, actor) for k, v in data.items()}
        if "can_remove" in out and "added_by" in out:
            out["can_remove"] = can_remove(actor, out)
        return out
    if isinstance(data, list):
        return [personalize(v, actor) for v in data]
    return data


def visible_to(sub: Subscription, event: Event) -> bool:
    """v1: tudo é visível. [futuro] convidado só vê player.state/queue.updated/library.updated/seus jobs."""
    return True


class EventBus:
    def __init__(self) -> None:
        self._subs: set[Subscription] = set()
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._listeners: list[Callable[[Event], None]] = []
        self._closing = False

    # ---- ciclo de vida ---------------------------------------------------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop or asyncio.get_running_loop()

    @property
    def closing(self) -> bool:
        return self._closing

    def shutdown(self) -> None:
        """Encerra todos os streams SSE e recusa novas assinaturas. Thread-safe e idempotente.

        Não pega locks nem bloqueia (só marca a flag e agenda `close_all` no loop), então é seguro chamar de um
        handler de sinal (`uvicorn.Server.handle_exit`) ou de outra thread.
        """
        self._closing = True
        loop = self._loop
        if loop is not None and not loop.is_closed():
            # sempre agendado (mesmo na thread do loop): num handler de sinal a thread pode estar dentro de `_lock`
            with contextlib.suppress(RuntimeError):  # loop fechou entre a checagem e a chamada
                loop.call_soon_threadsafe(self.close_all)
            return
        self.close_all()

    def subscribe(self, client_id: str, role: str, principal: Principal = HOST) -> Subscription:
        sub = Subscription(client_id=client_id, role=role, principal=principal)
        if self._closing:  # servidor encerrando: o stream termina logo após o `retry`
            sub.closed = True
            sub.queue.put_nowait(None)
            return sub
        with self._lock:
            self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        sub.closed = True
        with self._lock:
            self._subs.discard(sub)

    def add_listener(self, fn: Callable[[Event], None]) -> None:
        """Ouvinte interno chamado a cada `publish` (na thread de quem publicou). Não publique de dentro dele
        eventos do mesmo tipo que ele trata."""
        with self._lock:
            self._listeners.append(fn)

    def connected(self, client_id: str, role: str | None = None) -> bool:
        return any(s.client_id == client_id for s in self.subscribers(role))

    # ---- consultas -----------------------------------------------------------------
    def subscribers(self, role: str | None = None) -> list[Subscription]:
        with self._lock:
            return [s for s in self._subs if role is None or s.role == role]

    def count(self, role: str | None = None) -> int:
        return len(self.subscribers(role))

    # ---- publicação ------------------------------------------------------------------
    def make_event(self, type_: str, data: Any) -> Event:
        return Event(next(self._ids), type_, data)

    def publish(self, type_: str, data: Any) -> Event:
        """Envia a todos os assinantes. Thread-safe."""
        event = self.make_event(type_, data)
        loop = self._loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if loop is None or running is loop:
            self._deliver(event)
        elif not loop.is_closed():
            loop.call_soon_threadsafe(self._deliver, event)
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(event)
            except Exception:  # noqa: BLE001
                log.exception("erro no ouvinte de eventos (%s)", type_)
        return event

    def send_to(self, sub: Subscription, type_: str, data: Any) -> Event:
        """Envia só para um assinante (ex.: `hello`). Chame no loop do servidor."""
        event = self.make_event(type_, personalize(data, sub.principal.id))
        self._put(sub, event)
        return event

    def send_first(self, sub: Subscription, type_: str, data: Any) -> Event:
        """Como `send_to`, mas na frente dos eventos que já chegaram à fila da assinatura (ex.: `hello` calculado
        fora do loop: o que foi publicado enquanto o snapshot era montado vem depois dele). Chame no loop."""
        event = self.make_event(type_, personalize(data, sub.principal.id))
        if sub.closed:
            return event
        pending: list[Event | None] = []
        while not sub.queue.empty():
            pending.append(sub.queue.get_nowait())
        sub.queue.put_nowait(event)
        for item in pending:
            try:
                sub.queue.put_nowait(item)
            except asyncio.QueueFull:
                self._put(sub, event)  # estoura como em `_put`: desconecta (o cliente reconecta)
                break
        return event

    def _deliver(self, event: Event) -> None:
        per_actor: dict[str, Event] = {}
        for sub in self.subscribers():
            if not visible_to(sub, event):
                continue
            actor = sub.principal.id
            if actor not in per_actor:
                per_actor[actor] = Event(event.id, event.type, personalize(event.data, actor))
            self._put(sub, per_actor[actor])

    def _put(self, sub: Subscription, event: Event) -> None:
        if sub.closed:
            return
        try:
            sub.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.unsubscribe(sub)
            # garante que o stream termine
            while not sub.queue.empty():
                sub.queue.get_nowait()
            sub.queue.put_nowait(None)

    def close_all(self) -> None:
        """Encerramento, no loop do servidor: termina todo `sse_stream` aberto e recusa novas assinaturas.
        Idempotente. (De outra thread/handler de sinal, use `shutdown()`.)"""
        self._closing = True
        for sub in self.subscribers():
            self.unsubscribe(sub)
            with contextlib.suppress(asyncio.QueueFull):
                sub.queue.put_nowait(None)


async def sse_stream(
    bus: EventBus, sub: Subscription, keepalive_s: float = KEEPALIVE_S
) -> AsyncIterator[str]:
    """Gera o corpo `text/event-stream` de uma assinatura (retry + eventos + `: ping`)."""
    yield f"retry: {RETRY_MS}\n\n"
    try:
        while not sub.closed and not bus.closing:
            try:
                event = await asyncio.wait_for(sub.queue.get(), timeout=keepalive_s)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if event is None:
                break
            yield event.encode()
    finally:
        bus.unsubscribe(sub)
