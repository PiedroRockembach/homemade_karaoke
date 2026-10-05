"""Arquivos de mídia das músicas (Range/ETag/immutable) — contrato §2. Dono: backend-engineer (T-005).

Caminho sempre derivado de `song_id` validado por regex; nada de caminho vindo do cliente. O suporte a `Range`
(206/416) e `ETag`/`Last-Modified` vem do `FileResponse` do Starlette; `If-None-Match` → 304 é tratado aqui.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response

from karaoke.deps import SongId
from karaoke.errors import ApiError
from karaoke.security import scope

router = APIRouter(tags=["media"])

CACHE_CONTROL = "private, max-age=31536000, immutable"


def _serve(request: Request, song_id: str, name: str, media_type: str) -> Response:
    root = request.app.state.config.songs_dir.resolve()
    target = (root / song_id / name).resolve()
    if target.parent.parent != root or not target.is_file():
        raise ApiError(404, "song_not_found", "Arquivo da música não encontrado.", {"song_id": song_id})
    response = FileResponse(
        target, media_type=media_type, headers={"Cache-Control": CACHE_CONTROL}, stat_result=target.stat()
    )
    etag = response.headers.get("etag")
    inm = request.headers.get("if-none-match")
    if etag and inm and etag in [t.strip() for t in inm.split(",")]:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": CACHE_CONTROL})
    return response


@router.get("/media/songs/{song_id}/instrumental.opus", dependencies=[scope("host")])
def instrumental(song_id: SongId, request: Request) -> Response:
    return _serve(request, song_id, "instrumental.opus", "audio/ogg")


@router.get("/media/songs/{song_id}/vocals.opus", dependencies=[scope("host")])
def vocals(song_id: SongId, request: Request) -> Response:
    return _serve(request, song_id, "vocals.opus", "audio/ogg")


@router.get("/media/songs/{song_id}/thumb.jpg", dependencies=[scope("guest-safe")])
def thumb(song_id: SongId, request: Request) -> Response:
    return _serve(request, song_id, "thumb.jpg", "image/jpeg")
