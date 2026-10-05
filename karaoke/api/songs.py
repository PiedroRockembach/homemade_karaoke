"""Biblioteca — contrato §6. Dono: backend-engineer (T-005).

DELETE usa a regra de posse: `security.ensure_can_remove(actor, song, song_id)` → `403 not_owner`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from karaoke.deps import SongId
from karaoke.errors import invalid_request
from karaoke.security import current_actor, scope

router = APIRouter(tags=["songs"])


class SongPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    artist: str | None = Field(default=None, max_length=200)
    offset_ms: int | None = Field(default=None, ge=-10000, le=10000)


@router.get("/api/songs", dependencies=[scope("guest-safe")])
def list_songs(
    request: Request,
    q: str = Query(default="", max_length=200),
    status: str = Query(default="ready", pattern="^(ready|all)$"),
    sort: str = Query(default="recent", pattern="^(recent|title|artist)$"),
    actor: str = Depends(current_actor),
) -> dict:
    return request.app.state.library.list(q=q, status=status, sort=sort, actor=actor)


@router.get("/api/songs/{song_id}", dependencies=[scope("guest-safe")])
def get_song(song_id: SongId, request: Request, actor: str = Depends(current_actor)) -> dict:
    return request.app.state.library.get(song_id, actor)


@router.patch("/api/songs/{song_id}", dependencies=[scope("host")])
def patch_song(
    song_id: SongId, body: SongPatch, request: Request, actor: str = Depends(current_actor)
) -> dict:
    patch = body.model_dump(exclude_unset=True)
    if "title" in patch:
        title = " ".join((patch["title"] or "").split())
        if not title:
            raise invalid_request("O título não pode ficar vazio.", {"title": "obrigatório"})
        patch["title"] = title
    if "artist" in patch:
        patch["artist"] = " ".join((patch["artist"] or "").split()) or None
    if "offset_ms" in patch and patch["offset_ms"] is None:
        raise invalid_request("Offset inválido.", {"offset_ms": "não pode ser null"})
    return request.app.state.library.update(song_id, patch, actor)


@router.delete("/api/songs/{song_id}", status_code=204, dependencies=[scope("host")])
def delete_song(song_id: SongId, request: Request, actor: str = Depends(current_actor)) -> None:
    request.app.state.library.delete(song_id, actor)
