"""Ponto único de autorização (ADR-0002 §3, ADR-0003 §4, ARCHITECTURE §10) e regra de posse (api.md §0.2).

- `authorize` é dependência **global** do app: valida Host, Origin/Content-Type em mutações, resolve o `Principal`
  e compara o papel com o escopo declarado pela rota. Nenhum handler compara papéis.
- Rotas declaram escopo com `scope("host")`, `scope("guest-safe")` ou `scope("public")` (no APIRouter ou na rota).
  Sem marcador = `host` (deny-by-default).
- Papéis (v0.3): `host` (cliente loopback), `guest` (conexão pelo listener da LAN + cookie válido), `anon`
  (listener da LAN, sem sessão: só alcança `public`).
- `can_remove(actor, resource)` é a política única de remoção (DECISION 0006 do ciclo 20261005-0109): o host
  remove qualquer item; os demais só o que adicionaram (`added_by == actor`).
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from fastapi import Depends, Request
from fastapi.routing import APIRoute, iter_route_contexts

from karaoke.errors import forbidden, guest_session_required, invalid_request, not_owner

Role = Literal["host", "guest", "anon"]
ScopeName = Literal["host", "guest-safe", "public"]
SCOPES: tuple[str, ...] = ("host", "guest-safe", "public")
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True)
class Principal:
    role: Role
    id: str  # "host" | "g_<n>" | "anon"
    name: str | None = None


HOST = Principal(role="host", id="host")
ANON = Principal(role="anon", id="anon")


# ---- escopo por rota ---------------------------------------------------------
class _ScopeMarker:
    """Dependência sem efeito; serve de marcador lido por `route_scope`."""

    def __init__(self, name: ScopeName):
        if name not in SCOPES:
            raise ValueError(f"escopo inválido: {name}")
        self.name = name

    def __call__(self) -> None:
        return None

    def __repr__(self) -> str:
        return f"scope({self.name!r})"


def scope(name: ScopeName) -> Any:
    """Use em `APIRouter(dependencies=[scope("host")])` ou `@router.get(..., dependencies=[scope("guest-safe")])`.

    Se houver marcador na rota e no router, vale o da rota (o mais específico, último da lista).
    """
    return Depends(_ScopeMarker(name))


def declared_scope(route: APIRoute) -> str | None:
    """Escopo declarado explicitamente (None se a rota não declarou)."""
    found = None
    for dep in route.dependencies:
        if isinstance(dep.dependency, _ScopeMarker):
            found = dep.dependency.name
    return found


def route_scope(route: Any) -> str:
    """Escopo efetivo: o declarado ou `host` (deny-by-default)."""
    if isinstance(route, APIRoute):
        return declared_scope(route) or "host"
    return "host"


def iter_api_routes(app: Any) -> Iterator[tuple[str, APIRoute]]:
    """(caminho efetivo, APIRoute) de todas as rotas, inclusive as de routers incluídos (FastAPI ≥ 0.14x
    guarda routers incluídos como `_IncludedRouter`, então `app.routes` não é plano)."""
    for rc in iter_route_contexts(app.routes):
        if isinstance(rc.original_route, APIRoute):
            yield rc.path or rc.original_route.path, rc.original_route


def _find_route(request: Request) -> Any:
    route = request.scope.get("route")  # FastAPI preenche com a APIRoute original
    if route is not None:
        return route
    endpoint = request.scope.get("endpoint")
    for _, r in iter_api_routes(request.app):
        if r.endpoint is endpoint:
            return r
    return None


# ---- rate limit (no-op na v1) --------------------------------------------------
class RateLimiter(Protocol):
    def check(self, principal: Principal, request: Request) -> None: ...


class NoopRateLimiter:
    def check(self, principal: Principal, request: Request) -> None:
        return None


# ---- authorize -------------------------------------------------------------------
def _is_loopback_client(request: Request) -> bool:
    if request.client is None:
        return False
    try:
        return ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        return False


def _has_body(request: Request) -> bool:
    cl = request.headers.get("content-length")
    if cl is not None:
        return cl.strip() not in ("", "0")
    return "transfer-encoding" in request.headers


def _guest_service(request: Request) -> Any:
    return getattr(request.app.state, "guest", None)


def allowed_hosts(request: Request) -> frozenset[str]:
    """Hosts fixos do loopback ∪ `<ip-lan>:<porta>` só com o modo convidado ligado."""
    hosts = request.app.state.config.allowed_hosts
    guest = _guest_service(request)
    extra = guest.allowed_hosts() if guest is not None else frozenset()
    return hosts | extra if extra else hosts


def resolve_principal(request: Request) -> Principal:
    """Loopback → host. Pelo listener da LAN com o modo ligado: cookie válido → guest; senão → anon."""
    if _is_loopback_client(request):
        return HOST
    guest = _guest_service(request)
    if guest is None or not guest.accepts_connection(request):
        raise forbidden("guest_mode_off")
    session = guest.session_from_request(request)
    if session is not None:
        return Principal(role="guest", id=session.id, name=session.name)
    return ANON


async def authorize(request: Request) -> Principal:
    hosts = allowed_hosts(request)
    # 1. Host header (anti DNS-rebinding)
    if request.headers.get("host", "").lower() not in hosts:
        raise forbidden("host_not_allowed")
    # 2. Mutações: Origin (anti CSRF) e Content-Type JSON quando há corpo
    if request.method in MUTATING_METHODS:
        origin = request.headers.get("origin")
        if origin is not None and origin.lower() not in {f"http://{h}" for h in hosts}:
            raise forbidden("origin_not_allowed")
        if _has_body(request):
            ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype != "application/json":
                raise invalid_request("Envie o corpo como application/json.")
    # 3. Principal
    principal = resolve_principal(request)
    # 4. Escopo da rota × papel
    route_scope_name = route_scope(_find_route(request))
    if route_scope_name == "host" and principal.role != "host":
        raise forbidden("host_only")
    if route_scope_name == "guest-safe" and principal.role == "anon":
        raise guest_session_required()
    # 5. Rate limit de convidado/anon (o host nunca é limitado)
    limiter: RateLimiter = getattr(request.app.state, "rate_limiter", None) or NoopRateLimiter()
    limiter.check(principal, request)
    request.state.principal = principal
    return principal


def current_principal(request: Request) -> Principal:
    """Use nos handlers que precisam do ator: `principal: Principal = Depends(current_principal)`."""
    principal = getattr(request.state, "principal", None)
    if principal is None:  # authorize sempre roda antes; defesa em profundidade
        raise forbidden("no_principal")
    return principal


def current_actor(principal: Principal = Depends(current_principal)) -> str:
    """Id do ator atual (`"host"` ou `"g_<n>"`). É o valor gravado em `added_by`."""
    return principal.id


# ---- regra de posse ------------------------------------------------------------------
def _added_by(resource: Any) -> str | None:
    if isinstance(resource, Mapping):
        return resource.get("added_by")
    try:
        return resource["added_by"]  # sqlite3.Row
    except (TypeError, KeyError, IndexError):
        return getattr(resource, "added_by", None)


def can_remove(actor: str, resource: Any) -> bool:
    """Política única de remoção (QueueItem e Song), DECISION 0006: o host remove qualquer recurso com dono
    conhecido (modera a fila); os demais atores só o que eles mesmos adicionaram."""
    owner = _added_by(resource)
    if not actor or owner is None:
        return False
    return actor == HOST.id or owner == actor


def ensure_can_remove(actor: str, resource: Any, resource_id: str | None = None) -> None:
    """Levanta `403 not_owner` se `actor` não pode remover `resource`."""
    if not can_remove(actor, resource):
        raise not_owner(resource_id)
