"""Busca no YouTube e proxy de thumbnails — contrato §4 e §2 (`/api/thumbs`). Dono: backend-engineer (T-006)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from karaoke import youtube as yt
from karaoke.config import Config
from karaoke.db import Database
from karaoke.deps import SongId, get_config, get_db
from karaoke.errors import ApiError, invalid_request
from karaoke.security import scope

router = APIRouter(tags=["search"], dependencies=[scope("guest-safe")])

MAX_QUERY = 200
DEFAULT_LONG_S = 900
ACTIVE = ("queued", "model_download", "downloading", "separating", "lyrics", "finalizing")


def youtube_api_error(exc: yt.YoutubeError) -> ApiError:
    if exc.code == "network_unavailable":
        return ApiError(
            502, "network_unavailable", "A busca exige internet. Verifique a conexão e tente de novo."
        )
    details = {"hint": exc.hint or yt.UPDATE_HINT}
    return ApiError(502, "youtube_error", "O YouTube não respondeu como esperado.", details)


def long_video_threshold_s(db: Database, config: Config) -> int:
    try:
        from karaoke.api.settings import get_settings

        return int(get_settings(db, config).get("long_video_warn_s") or DEFAULT_LONG_S)
    except Exception:  # noqa: BLE001
        return DEFAULT_LONG_S


def library_status(db: Database, ids: list[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    conn = db.connect()
    try:
        marks = ",".join("?" * len(ids))
        songs = conn.execute(f"SELECT id, status FROM songs WHERE id IN ({marks})", ids).fetchall()  # noqa: S608
        jobs = conn.execute(
            f"SELECT song_id, id FROM jobs WHERE song_id IN ({marks}) AND kind = 'process' "  # noqa: S608
            f"AND stage IN ({','.join(repr(s) for s in ACTIVE)}) ORDER BY created_at",
            ids,
        ).fetchall()
    finally:
        conn.close()
    active = {r["song_id"]: r["id"] for r in jobs}
    out: dict[str, dict[str, Any]] = {}
    for s in songs:
        if s["status"] == "ready":
            out[s["id"]] = {"status": "ready", "job_id": None}
        elif s["status"] in ("queued", "processing") or s["id"] in active:
            out[s["id"]] = {"status": "processing", "job_id": active.get(s["id"])}
        else:
            out[s["id"]] = {"status": "error", "job_id": None}
    return out


@router.get("/api/search")
def search(
    q: str = Query(default=""),
    limit: int = Query(default=15, ge=1, le=25),
    config: Config = Depends(get_config),
    db: Database = Depends(get_db),
) -> dict:
    query = " ".join(q.split())
    if not query:
        raise invalid_request("Digite o que você quer buscar.", {"q": "obrigatório"})
    if len(query) > MAX_QUERY:
        raise invalid_request("Busca muito longa.", {"q": f"máximo {MAX_QUERY} caracteres"})
    try:
        results = yt.search(query, limit)
    except yt.YoutubeError as exc:
        raise youtube_api_error(exc) from exc
    threshold_ms = long_video_threshold_s(db, config) * 1000
    lib = library_status(db, [r.youtube_id for r in results])
    return {
        "query": query,
        "results": [
            {
                "youtube_id": r.youtube_id,
                "title": r.title,
                "channel": r.channel,
                "duration_ms": r.duration_ms,
                "thumbnail": f"/api/thumbs/{r.youtube_id}",
                "url": r.url,
                "is_long": bool(r.duration_ms and r.duration_ms > threshold_ms),
                "is_live": r.is_live,
                "library": lib.get(r.youtube_id),
            }
            for r in results
        ],
    }


def _thumb_cache(request: Request, config: Config) -> yt.ThumbCache:
    cache = getattr(request.app.state, "thumb_cache", None)
    if cache is None:
        cache = request.app.state.thumb_cache = yt.ThumbCache(config.cache_dir / "thumbs")
    return cache


@router.get("/api/thumbs/{youtube_id}")
async def thumb(youtube_id: SongId, request: Request, config: Config = Depends(get_config)) -> FileResponse:
    path = await run_in_threadpool(_thumb_cache(request, config).get, youtube_id)
    if path is None:
        raise ApiError(404, "not_found", "Thumbnail indisponível.", {"youtube_id": youtube_id})
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
