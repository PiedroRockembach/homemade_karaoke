"""Ponto único de autorização (ADR-0002 §3, ARCHITECTURE §10) e regra de posse (SPEC AC-04.4/AC-05.5, DECISION 0009/0012).

- `authorize` é dependência **global** do app: valida Host, Origin/Content-Type em mutações, resolve o `Principal`
  e compara o papel com o escopo declarado pela rota. Nenhum handler compara papéis.
- Rotas declaram escopo com `scope("host")` ou `scope("guest-safe")` (no APIRouter ou na rota). Sem marcador = `host`.
- `can_remove(actor, resource)` é a política única de remoção: só quem adicionou (`added_by == actor`) remove —
  **sem exceção para o host**.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from fastapi import Depends, Request
from fastapi.routing import APIRoute, iter_route_contexts

from karaoke.errors import forbidden, invalid_request, not_owner

Role = Literal["host", "guest"]
ScopeName = Literal["host", "guest-safe"]
SCOPES: tuple[str, ...] = ("host", "guest-safe")
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True)
class Principal:
    role: Role
    id: str  # "host" | "g_<n>" (futuro, ADR-0002)
    name: str | None = None


HOST = Principal(role="host", id="host")


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


def resolve_principal(request: Request) -> Principal:
    """v1: só o ramo host (loopback). [futuro] cookie de convidado → Principal(guest)."""
    if _is_loopback_client(request):
        return HOST
    raise forbidden("non_loopback_client")


async def authorize(request: Request) -> Principal:
    config = request.app.state.config
    # 1. Host header (anti DNS-rebinding)
    if request.headers.get("host", "").lower() not in config.allowed_hosts:
        raise forbidden("host_not_allowed")
    # 2. Mutações: Origin (anti CSRF) e Content-Type JSON quando há corpo
    if request.method in MUTATING_METHODS:
        origin = request.headers.get("origin")
        if origin is not None and origin.lower() not in config.allowed_origins:
            raise forbidden("origin_not_allowed")
        if _has_body(request):
            ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype != "application/json":
                raise invalid_request("Envie o corpo como application/json.")
    # 3. Principal
    principal = resolve_principal(request)
    # 4. Escopo da rota × papel
    if route_scope(_find_route(request)) == "host" and principal.role != "host":
        raise forbidden("host_only")
    # 5. Rate limit (no-op v1)
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
    """Id do ator atual (`"host"` na v1). É o valor gravado em `added_by`."""
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
    """Só quem adicionou remove (QueueItem e Song). Sem exceção para o host (DECISION 0012)."""
    owner = _added_by(resource)
    return bool(actor) and owner is not None and owner == actor


def ensure_can_remove(actor: str, resource: Any, resource_id: str | None = None) -> None:
    """Levanta `403 not_owner` se `actor` não pode remover `resource`."""
    if not can_remove(actor, resource):
        raise not_owner(resource_id)
