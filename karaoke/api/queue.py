"""Fila — contrato §8. Dono: backend-engineer (T-005).

- `POST /api/queue` grava `added_by = actor` (de `current_actor`), nunca do corpo.
- `DELETE /api/queue/{id}` usa `security.ensure_can_remove(actor, item, item_id)` → `403 not_owner`.
- v0.3 (T-004): `POST` e `DELETE /{id}` são `guest-safe`; as restrições de convidado (só `end`, `singer` padrão,
  `added_by_name`, `409 queue_item_playing`) ficam em `karaoke/guest/policy.py`. Sem cotas (DECISION 0016).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from karaoke.deps import QUEUE_ITEM_ID_PATTERN, SONG_ID_PATTERN, QueueItemId
from karaoke.security import Principal, current_actor, current_principal, scope

router = APIRouter(tags=["queue"])


class QueueAdd(BaseModel):
    model_config = ConfigDict(extra="ignore")  # `added_by` no corpo é ignorado (definido pelo servidor)

    song_id: str = Field(pattern=SONG_ID_PATTERN)
    singer: str | None = Field(default=None, max_length=200)
    position: Literal["end", "next"] | int = "end"

    @model_validator(mode="after")
    def _check_position(self) -> QueueAdd:
        if isinstance(self.position, int) and (isinstance(self.position, bool) or self.position < 0):
            raise ValueError("position deve ser 'end', 'next' ou índice >= 0")
        return self


class QueuePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    singer: str | None = Field(default=None, max_length=200)


class QueueReorder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int | None = None
    item_ids: list[str] = Field(max_length=1000)

    @model_validator(mode="after")
    def _check_ids(self) -> QueueReorder:
        import re

        if any(not re.fullmatch(QUEUE_ITEM_ID_PATTERN, i) for i in self.item_ids):
            raise ValueError("item_ids deve conter ids q_<n>")
        return self


class QueueMove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_index: int | None = Field(default=None, ge=0)
    direction: Literal["up", "down", "top", "bottom"] | None = None

    @model_validator(mode="after")
    def _one_of(self) -> QueueMove:
        if (self.to_index is None) == (self.direction is None):
            raise ValueError("informe to_index ou direction (apenas um)")
        return self


@router.get("/api/queue", dependencies=[scope("guest-safe")])
def get_queue(request: Request, actor: str = Depends(current_actor)) -> dict:
    return request.app.state.queue.snapshot(actor)


@router.post("/api/queue", status_code=201, dependencies=[scope("guest-safe")])
def add_to_queue(body: QueueAdd, request: Request, principal: Principal = Depends(current_principal)) -> dict:
    return request.app.state.queue.add(body.song_id, body.singer, body.position, principal.id, principal)


@router.delete("/api/queue", status_code=204, dependencies=[scope("host")])
def clear_queue(request: Request) -> None:
    request.app.state.queue.clear()


@router.post("/api/queue/reorder", dependencies=[scope("host")])
def reorder_queue(body: QueueReorder, request: Request, actor: str = Depends(current_actor)) -> dict:
    return request.app.state.queue.reorder(body.item_ids, body.revision, actor)


@router.patch("/api/queue/{item_id}", dependencies=[scope("host")])
def patch_item(
    item_id: QueueItemId, body: QueuePatch, request: Request, actor: str = Depends(current_actor)
) -> dict:
    if "singer" not in body.model_fields_set:
        return request.app.state.queue.get_item(item_id, actor)
    return request.app.state.queue.update(item_id, body.singer, actor)


@router.delete("/api/queue/{item_id}", status_code=204, dependencies=[scope("guest-safe")])
def remove_item(
    item_id: QueueItemId, request: Request, principal: Principal = Depends(current_principal)
) -> None:
    request.app.state.queue.remove(item_id, principal.id, principal)


@router.post("/api/queue/{item_id}/move", dependencies=[scope("host")])
def move_item(
    item_id: QueueItemId, body: QueueMove, request: Request, actor: str = Depends(current_actor)
) -> dict:
    return request.app.state.queue.move(item_id, body.to_index, body.direction, actor)
