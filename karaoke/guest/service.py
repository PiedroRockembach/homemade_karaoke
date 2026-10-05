"""`GuestService`: convite, sessões e estado do modo convidado (ADR-0003 §1, §4, §7; api.md §12).

Tudo só em memória: a cada reinício o modo volta a desligado (AC-11.5). `startup(app)` cria o serviço em
`app.state.guest` e o timer de 60 s (rotação automática do convite expirado e expiração de sessões); `shutdown(app)`
fecha o listener da LAN.

Concorrência: operações do anfitrião (`enable`/`disable`/`rotate`/`end_sessions`) rodam no loop, serializadas por um
`asyncio.Lock`. Sessões ficam num dict protegido por `threading.Lock` (consultas rápidas no `authorize`).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request

from karaoke.db import next_id
from karaoke.errors import (
    ApiError,
    forbidden,
    guest_invite_invalid,
    guest_mode_off,
    invalid_request,
    lan_unavailable,
    rate_limited,
)
from karaoke.events import Subscription
from karaoke.guest import netinfo
from karaoke.guest.listener import LanListener
from karaoke.guest.netinfo import LanAddress
from karaoke.guest.qr import qr_svg
from karaoke.guest.ratelimit import BUCKETS
from karaoke.security import Principal

log = logging.getLogger("karaoke")

COOKIE_NAME = "karaoke_guest"
INVITE_TTL_S = 12 * 3600
SESSION_TTL_S = 12 * 3600
MAX_SESSIONS = 50  # sessões **ativas** (review-1 M-1; DECISION 0016: teto ≥ 30): as inativas não ocupam vaga
MAX_STORED_SESSIONS = 4 * MAX_SESSIONS  # teto de memória: acima disso, despeja as inativas mais antigas
SESSION_IDLE_S = 15 * 60  # sem requisição nem SSE aberto há mais que isso → inativa (continua válida)
SSE_MAX_TOTAL = 30
SSE_MAX_PER_GUEST = 3
SSE_RETRY_AFTER_S = 5
TICK_S = 60.0
STATE_MIN_INTERVAL_S = 1.0
SEARCH_PER_MIN = int(BUCKETS["search"].per_min)  # GuestMe.limits (DECISION 0019: 30)
DEFAULT_LONG_VIDEO_S = 900


def _hash(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def set_cookie_header(value: str) -> str:
    return f"{COOKIE_NAME}={value}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_TTL_S}"


CLEAR_COOKIE_HEADER = f"{COOKIE_NAME}=; Max-Age=0; Path=/"


@dataclass
class GuestInvite:
    token_hash: bytes
    url: str  # pronta para o QR (contém o token): só sai por rotas host e `guest.state`
    version: int
    expires_at: float


@dataclass
class GuestSession:
    id: str
    name: str
    cookie_hash: bytes
    created_at: float
    expires_at: float
    last_seen: float
    ip: str = "?"


class GuestService:
    def __init__(
        self,
        app: FastAPI,
        *,
        discover: Callable[[], list[LanAddress]] = netinfo.discover,
        listener: Any = None,
        clock: Callable[[], float] = time.time,
    ):
        self.app = app
        self.config = app.state.config
        self.discover = discover
        self.listener = listener if listener is not None else LanListener(app)
        self.clock = clock
        self.enabled = False
        self.address: str | None = None
        self.invite: GuestInvite | None = None
        self.error: dict[str, str] | None = None
        self._sessions: dict[bytes, GuestSession] = {}
        self._lock = threading.Lock()
        self._op_lock = asyncio.Lock()
        self._version = 0
        self._qr_cache: tuple[int, bytes] | None = None
        self._timer: asyncio.Task[None] | None = None
        self._last_state_at = 0.0
        self._state_scheduled = False

    @property
    def port(self) -> int:
        return self.config.port

    # ---- consultas usadas pelo authorize ------------------------------------------------------------
    def allowed_hosts(self) -> frozenset[str]:
        if self.enabled and self.address:
            return frozenset({f"{self.address}:{self.port}"})
        return frozenset()

    def accepts_connection(self, request: Request) -> bool:
        """Conexão chegou pelo listener da LAN com o modo ligado."""
        if not self.enabled or not self.address:
            return False
        server = request.scope.get("server")
        return bool(server) and server[0] == self.address

    def session_from_request(self, request: Request) -> GuestSession | None:
        value = request.cookies.get(COOKIE_NAME)
        if not value or len(value) > 128:
            return None
        return self.session_from_cookie(value)

    def session_from_cookie(self, value: str) -> GuestSession | None:
        key = _hash(value)
        now = self.clock()
        expired = False
        with self._lock:
            session = self._sessions.get(key)
            if session is not None and session.expires_at <= now:
                del self._sessions[key]
                session, expired = None, True
            elif session is not None:
                session.last_seen = now
        if expired:
            self.notify_state()
        return session

    @property
    def session_count(self) -> int:
        now = self.clock()
        with self._lock:
            return sum(1 for s in self._sessions.values() if s.expires_at > now)

    def _is_active(self, session: GuestSession, now: float, connected: set[str]) -> bool:
        return session.expires_at > now and (
            session.last_seen > now - SESSION_IDLE_S or session.id in connected
        )

    def _connected_ids(self) -> set[str]:
        bus = getattr(self.app.state, "bus", None)
        return bus.principals("guest") if bus is not None else set()

    def sessions(self) -> list[GuestSession]:
        with self._lock:
            return list(self._sessions.values())

    # ---- estado ---------------------------------------------------------------------------------------
    def addresses(self) -> list[LanAddress]:
        try:
            return list(self.discover())
        except Exception:  # noqa: BLE001
            log.exception("falha ao listar as interfaces de rede")
            return []

    def state(self) -> dict[str, Any]:
        """`GuestState` (api.md §12.1) — só para o host (contém a URL com o token)."""
        on = self.enabled and self.invite is not None
        invite = self.invite if on else None
        return {
            "enabled": on,
            "url": invite.url if invite else None,
            "display_url": f"{self.address}:{self.port}/guest" if on else None,
            "qr": f"/api/guest/qr?v={invite.version}" if invite else None,
            "invite_version": invite.version if invite else 0,
            "invite_expires_at": _iso(invite.expires_at) if invite else None,
            "sessions": self.session_count if on else 0,
            "connected": len(self.app.state.bus.principals("guest")) if on else 0,
            "address": self.address if on else None,
            "port": self.port,
            "addresses": [a.to_dict() for a in self.addresses()],
            "firewall_hint": netinfo.firewall_hint(self.port),
            "error": self.error,
        }

    def publish_state(self) -> None:
        self._last_state_at = time.monotonic()
        self.app.state.bus.publish("guest.state", self.state())

    def notify_state(self) -> None:
        """`guest.state` no máximo 1 por segundo (sessão criada/expirada, SSE de convidado aberto/fechado)."""
        wait = STATE_MIN_INTERVAL_S - (time.monotonic() - self._last_state_at)
        if wait <= 0:
            self.publish_state()
            return
        if self._state_scheduled:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.publish_state()
            return
        self._state_scheduled = True

        def fire() -> None:
            self._state_scheduled = False
            self.publish_state()

        loop.call_later(wait, fire)

    def me(self, session: GuestSession) -> dict[str, Any]:
        """`GuestMe` (api.md §12.1)."""
        return {
            "id": session.id,
            "name": session.name,
            "expires_at": _iso(session.expires_at),
            "limits": {
                "max_duration_ms": self._long_video_s() * 1000,
                "search_per_min": SEARCH_PER_MIN,
            },
        }

    def _long_video_s(self) -> int:
        from karaoke.api.settings import read_settings

        try:
            conn = self.app.state.db.connect()
            try:
                return int(read_settings(conn, self.config).get("long_video_warn_s") or DEFAULT_LONG_VIDEO_S)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            log.exception("falha ao ler long_video_warn_s")
            return DEFAULT_LONG_VIDEO_S

    def qr(self) -> bytes:
        invite = self.invite
        if not self.enabled or invite is None:
            raise guest_mode_off()
        cached = self._qr_cache
        if cached is None or cached[0] != invite.version:
            cached = (invite.version, qr_svg(invite.url))
            self._qr_cache = cached
        return cached[1]

    # ---- convite ----------------------------------------------------------------------------------------
    def _new_invite(self) -> None:
        token = secrets.token_urlsafe(16)
        self._version += 1
        self.invite = GuestInvite(
            token_hash=_hash(token),
            url=f"http://{self.address}:{self.port}/guest#t={token}",
            version=self._version,
            expires_at=self.clock() + INVITE_TTL_S,
        )

    def _invite_valid(self, token: str) -> bool:
        invite = self.invite
        if not self.enabled or invite is None or invite.expires_at <= self.clock():
            return False
        return hmac.compare_digest(_hash(token), invite.token_hash)

    # ---- sessões ------------------------------------------------------------------------------------------
    def create_session(
        self, token: str, name: str, old_cookie: str | None = None, ip: str = "?"
    ) -> tuple[GuestSession, str]:
        """Valida o convite e cria a sessão. Devolve (sessão, valor do cookie).

        Review-1 M-1: cookie válido → a sessão é **substituída** (api.md §12.3). Só sessões ativas contam para
        `MAX_SESSIONS`. Checagem e inserção acontecem sob o mesmo lock (sem TOCTOU). Não há limite por IP
        (DECISION 0016: vários convidados atrás do mesmo NAT entram normalmente); o `ip` fica só como registro.
        L-2: os streams SSE da sessão substituída/despejada são fechados.
        """
        from karaoke.guest.policy import sanitize_name

        if not self.enabled:
            raise forbidden("guest_mode_off")
        if not isinstance(token, str) or not token or len(token) > 128:
            raise invalid_request("Escaneie o QR da TV.", {"token": "obrigatório"})
        if not self._invite_valid(token):
            raise guest_invite_invalid()
        clean = sanitize_name(name)
        with self.app.state.db.tx() as conn:  # id reservado antes do lock (um id perdido não faz mal)
            sid = f"g_{next_id(conn, 'guest')}"
        cookie = secrets.token_urlsafe(32)
        connected = self._connected_ids()
        replaced: set[str] = set()
        evicted: set[str] = set()
        with self._lock:
            now = self.clock()
            for key in [k for k, s in self._sessions.items() if s.expires_at <= now]:
                del self._sessions[key]
            old = self._sessions.get(_hash(old_cookie)) if old_cookie and len(old_cookie) <= 128 else None
            others = [s for s in self._sessions.values() if old is None or s.id != old.id]
            active = sum(1 for s in others if self._is_active(s, now, connected))
            if active >= MAX_SESSIONS:
                raise rate_limited(60, "too_many_guests")
            # a partir daqui a sessão será criada
            if old is not None:
                del self._sessions[old.cookie_hash]
                replaced.add(old.id)
            overflow = len(self._sessions) + 1 - MAX_STORED_SESSIONS
            if overflow > 0:
                idle = sorted(
                    (s for s in self._sessions.values() if not self._is_active(s, now, connected)),
                    key=lambda s: s.last_seen,
                )
                for victim in idle[:overflow]:
                    del self._sessions[victim.cookie_hash]
                    evicted.add(victim.id)
            session = GuestSession(
                id=sid,
                name=clean,
                cookie_hash=_hash(cookie),
                created_at=now,
                expires_at=now + SESSION_TTL_S,
                last_seen=now,
                ip=ip,
            )
            self._sessions[session.cookie_hash] = session
        bus = self.app.state.bus
        if replaced:
            bus.close_role("guest", where=lambda sub: sub.principal.id in replaced)
        if evicted:
            log.info("modo convidado: sessões despejadas %s", sorted(evicted))
            self._close_guests("ended", evicted)
        log.info("modo convidado: sessão %s criada", sid)
        self.notify_state()
        return session, cookie

    def end_session(self, session_id: str) -> None:
        """Sair (`DELETE /api/guest/session`): apaga a sessão e fecha os streams dela."""
        with self._lock:
            for key in [k for k, s in self._sessions.items() if s.id == session_id]:
                del self._sessions[key]
        self.app.state.bus.close_role("guest", where=lambda sub: sub.principal.id == session_id)
        self.notify_state()

    def _close_guests(self, reason: str, ids: set[str] | None = None) -> None:
        def match(sub: Subscription) -> bool:
            return ids is None or sub.principal.id in ids

        self.app.state.bus.close_role("guest", final=("guest.ended", {"reason": reason}), where=match)

    # ---- SSE de convidado ------------------------------------------------------------------------------------
    def ensure_sse_slot(self, principal: Principal) -> None:
        bus = self.app.state.bus
        if (
            bus.count("guest") >= SSE_MAX_TOTAL
            or bus.count_principal(principal.id, "guest") >= SSE_MAX_PER_GUEST
        ):
            raise rate_limited(SSE_RETRY_AFTER_S, "sse_limit")

    # ---- operações do anfitrião --------------------------------------------------------------------------------
    async def enable(self, address: str | None = None) -> dict[str, Any]:
        async with self._op_lock:
            candidates = self.addresses()
            if not candidates:
                self.error = {"code": "lan_unavailable", "message": lan_unavailable().message}
                self.publish_state()
                raise lan_unavailable()
            if address is not None and address not in {a.address for a in candidates}:
                raise invalid_request(
                    "Escolha um dos endereços da lista.", {"address": "endereço desconhecido"}
                )
            if address is None:
                address = next((a.address for a in candidates if a.recommended), candidates[0].address)
            if self.enabled and self.address == address and self.listener.running:
                return self.state()
            if self.enabled:  # troca de IP: as sessões caem (o cookie é por host)
                await self._turn_off("disabled")
            self.enabled, self.address = True, address
            self._new_invite()
            try:
                await self.listener.start(address, self.port)
            except ApiError as exc:
                self.enabled, self.address, self.invite = False, None, None
                self.error = {"code": exc.code, "message": exc.message}
                self.publish_state()
                raise
            self.error = None
            log.info("modo convidado ligado em %s:%s", address, self.port)
            self.publish_state()
            return self.state()

    async def disable(self) -> dict[str, Any]:
        async with self._op_lock:
            if self.enabled or self.listener.running:
                await self._turn_off("disabled")
                log.info("modo convidado desligado")
            self.error = None
            self.publish_state()
            return self.state()

    async def _turn_off(self, reason: str) -> None:
        # ordem da ADR-0003 §1: recusar convidados → guest.ended + fechar streams → parar o listener
        self.enabled = False
        self._close_guests(reason)
        with self._lock:
            self._sessions.clear()
        self.invite = None
        self.address = None
        self._qr_cache = None
        await self.listener.stop()

    async def rotate(self) -> dict[str, Any]:
        async with self._op_lock:
            if not self.enabled:
                raise guest_mode_off()
            self._new_invite()
            self.publish_state()
            return self.state()

    async def end_sessions(self) -> dict[str, Any]:
        async with self._op_lock:
            if not self.enabled:
                raise guest_mode_off()
            with self._lock:
                self._sessions.clear()
            self._close_guests("ended")
            self._new_invite()  # sem rotação, o link antigo deixaria todos entrarem de novo
            self.publish_state()
            return self.state()

    # ---- timer ---------------------------------------------------------------------------------------------------
    async def tick(self) -> None:
        """Rotação automática do convite expirado e expiração de sessões (a cada `TICK_S`)."""
        async with self._op_lock:
            if not self.enabled:
                return
            now = self.clock()
            changed = False
            if self.invite is not None and self.invite.expires_at <= now:
                self._new_invite()
                changed = True
            with self._lock:
                expired = {k: s for k, s in self._sessions.items() if s.expires_at <= now}
                for key in expired:
                    del self._sessions[key]
            if expired:
                self._close_guests("expired", {s.id for s in expired.values()})
                changed = True
            if changed:
                self.publish_state()

    async def _run_timer(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("erro no timer do modo convidado")

    def request_stop(self) -> None:
        """Encerramento do app (1º sinal): síncrono, sem lock."""
        self.enabled = False
        self.listener.request_stop()

    async def start(self) -> None:
        self._timer = asyncio.create_task(self._run_timer(), name="guest-timer")

    async def stop(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._timer
            self._timer = None
        self.enabled = False
        with contextlib.suppress(Exception):
            self._close_guests("disabled")
        with self._lock:
            self._sessions.clear()
        self.invite = None
        await self.listener.stop()


# ---- ciclo de vida (app.LIFECYCLE_MODULES) ------------------------------------------------------------------
async def startup(app: FastAPI) -> None:
    service = getattr(app.state, "guest", None)
    if service is None:
        service = GuestService(app)
        app.state.guest = service
    await service.start()


async def shutdown(app: FastAPI) -> None:
    service = getattr(app.state, "guest", None)
    if service is not None:
        await service.stop()
