"""Player e posse do áudio do Palco — contrato §9. Dono: backend-engineer (T-005)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from karaoke.security import current_actor, scope
from karaoke.services.player import ACTIONS, REPORT_EVENTS

router = APIRouter(tags=["player"])

CLIENT_ID_PATTERN = r"^c_[A-Za-z0-9_-]{1,64}$"


class Command(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: Literal[ACTIONS]  # type: ignore[valid-type]
    value: Any = None
    client_id: str | None = Field(default=None, pattern=CLIENT_ID_PATTERN)


class ReportError(BaseModel):
    code: str = Field(max_length=64)
    message: str | None = Field(default=None, max_length=500)


class Report(BaseModel):
    model_config = ConfigDict(extra="ignore")

    client_id: str = Field(pattern=CLIENT_ID_PATTERN)
    queue_item_id: str | None = Field(default=None, pattern=r"^q_[0-9]+$")
    song_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{11}$")
    position_ms: int = Field(default=0, ge=0)
    playing: bool = False
    event: Literal[REPORT_EVENTS] = "tick"  # type: ignore[valid-type]
    error: ReportError | None = None


class Claim(BaseModel):
    model_config = ConfigDict(extra="ignore")

    client_id: str = Field(pattern=CLIENT_ID_PATTERN)


@router.get("/api/player", dependencies=[scope("guest-safe")])
def get_player(request: Request, actor: str = Depends(current_actor)) -> dict:
    return request.app.state.player.state(actor)


@router.post("/api/player/command", dependencies=[scope("host")])
def command(body: Command, request: Request, actor: str = Depends(current_actor)) -> dict:
    return request.app.state.player.command(body.action, body.value, actor)


@router.post("/api/player/report", status_code=204, dependencies=[scope("host")])
def report(body: Report, request: Request) -> None:
    request.app.state.player.report(body.model_dump())


@router.post("/api/stage/claim", dependencies=[scope("host")])
def stage_claim(body: Claim, request: Request, actor: str = Depends(current_actor)) -> dict:
    return request.app.state.player.claim(body.client_id, actor)
