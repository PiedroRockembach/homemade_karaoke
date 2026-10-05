"""Rotas do modo convidado — api.md §12 (ADR-0003). Dono: backend-engineer (T-003).

- §12.2 `[host]`: `GET /api/guest/state`, `POST /api/guest/{enable,disable,rotate,end-sessions}`, `GET /api/guest/qr`.
- §12.3: `POST`/`GET /api/guest/session` `[public]`, `DELETE /api/guest/session` `[guest-safe]`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from karaoke.errors import forbidden, guest_session_required
from karaoke.guest.service import CLEAR_COOKIE_HEADER, COOKIE_NAME, GuestService, set_cookie_header
from karaoke.security import Principal, current_principal, scope

router = APIRouter(tags=["guest"], dependencies=[scope("host")])


def get_guest(request: Request) -> GuestService:
    return request.app.state.guest


class EnableBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: str | None = Field(default=None, max_length=64)


class SessionBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    token: str = Field(min_length=1, max_length=128)
    name: str = Field(max_length=400)  # o limite real (1–40 após saneamento) é aplicado pelo serviço


# ---- §12.2 anfitrião ---------------------------------------------------------------------------------
@router.get("/api/guest/state")
async def guest_state(guest: GuestService = Depends(get_guest)) -> dict[str, Any]:
    return guest.state()


@router.post("/api/guest/enable")
async def guest_enable(
    body: EnableBody | None = None, guest: GuestService = Depends(get_guest)
) -> dict[str, Any]:
    return await guest.enable(body.address if body else None)


@router.post("/api/guest/disable")
async def guest_disable(guest: GuestService = Depends(get_guest)) -> dict[str, Any]:
    return await guest.disable()


@router.post("/api/guest/rotate")
async def guest_rotate(guest: GuestService = Depends(get_guest)) -> dict[str, Any]:
    return await guest.rotate()


@router.post("/api/guest/end-sessions")
async def guest_end_sessions(guest: GuestService = Depends(get_guest)) -> dict[str, Any]:
    return await guest.end_sessions()


@router.get("/api/guest/qr")
async def guest_qr(guest: GuestService = Depends(get_guest)) -> Response:
    return Response(guest.qr(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


# ---- §12.3 convidado ----------------------------------------------------------------------------------
@router.post("/api/guest/session", status_code=201, dependencies=[scope("public")])
async def guest_session_create(
    body: SessionBody,
    request: Request,
    principal: Principal = Depends(current_principal),
    guest: GuestService = Depends(get_guest),
) -> JSONResponse:
    if principal.role == "host":  # o host (loopback) não cria sessão de convidado
        raise forbidden("host_client")
    ip = request.client.host if request.client else "?"
    session, cookie = guest.create_session(body.token, body.name, request.cookies.get(COOKIE_NAME), ip)
    return JSONResponse(
        {"guest": guest.me(session)},
        status_code=201,
        headers={"Set-Cookie": set_cookie_header(cookie), "Cache-Control": "no-store"},
    )


@router.get("/api/guest/session", dependencies=[scope("public")])
async def guest_session_get(
    principal: Principal = Depends(current_principal), guest: GuestService = Depends(get_guest)
) -> dict[str, Any]:
    session = _session_of(principal, guest)
    return {"guest": guest.me(session)}


@router.delete("/api/guest/session", status_code=204, dependencies=[scope("guest-safe")])
async def guest_session_delete(
    principal: Principal = Depends(current_principal), guest: GuestService = Depends(get_guest)
) -> Response:
    if principal.role == "guest":
        guest.end_session(principal.id)
    return Response(status_code=204, headers={"Set-Cookie": CLEAR_COOKIE_HEADER})


def _session_of(principal: Principal, guest: GuestService) -> Any:
    if principal.role != "guest":
        raise guest_session_required()
    for session in guest.sessions():
        if session.id == principal.id:
            return session
    raise guest_session_required()
