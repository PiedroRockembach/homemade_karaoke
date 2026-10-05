"""Jobs de processamento — contrato §5. Dono: backend-engineer (T-006)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from karaoke import youtube as yt
from karaoke.api.search import long_video_threshold_s, youtube_api_error
from karaoke.db import utcnow
from karaoke.deps import SONG_ID_PATTERN, JobId
from karaoke.errors import ApiError, invalid_request
from karaoke.jobs.runner import JobRunner, song_payload
from karaoke.lyrics.titleclean import clean_title
from karaoke.security import current_actor, scope

router = APIRouter(tags=["jobs"])


class CreateJob(BaseModel):
    model_config = ConfigDict(extra="ignore")

    youtube_id: str = Field(pattern=SONG_ID_PATTERN)
    title: str | None = Field(default=None, max_length=500)
    channel: str | None = Field(default=None, max_length=300)
    duration_ms: int | None = Field(default=None, ge=0, le=24 * 3600 * 1000)
    enqueue: bool = False
    singer: str | None = Field(default=None, max_length=200)
    confirm_long: bool = False
    lyrics_mode: Literal["aligned", "transcribe", "online"] | None = None  # override só para este job


def job_not_found(job_id: str) -> ApiError:
    return ApiError(404, "job_not_found", "Processamento não encontrado.", {"job_id": job_id})


def _runner(request: Request) -> JobRunner:
    return request.app.state.runner


def _enqueue(request: Request, song_id: str, singer: str | None, actor: str) -> dict[str, Any] | None:
    queue = getattr(request.app.state, "queue", None)
    if queue is None:
        return None
    return queue.add(song_id, singer, "end", actor)


@router.post("/api/jobs", status_code=201, dependencies=[scope("guest-safe")])
def create_job(body: CreateJob, request: Request, actor: str = Depends(current_actor)) -> Any:
    app = request.app
    db, config = app.state.db, app.state.config
    runner = _runner(request)
    sid = body.youtube_id

    # 1. já pronta ou já em processamento → não duplica
    conn = db.connect()
    try:
        existing = conn.execute("SELECT * FROM songs WHERE id = ?", (sid,)).fetchone()
        active = runner.active_job_for(conn, sid, "process")
    finally:
        conn.close()
    if existing is not None and (existing["status"] == "ready" or active is not None):
        item = _enqueue(request, sid, body.singer, actor) if body.enqueue else None
        conn = db.connect()
        try:
            song = song_payload(conn, sid, actor)
            job = (
                runner.job_dict(active["id"], conn)
                if active is not None and existing["status"] != "ready"
                else None
            )
        finally:
            conn.close()
        return JSONResponse({"job": job, "song": song, "queue_item": item}, status_code=200)

    # 2. metadados (título/duração) — da busca ou do YouTube
    title, channel, duration_ms = body.title, body.channel, body.duration_ms
    if existing is not None:
        title = title or existing["original_title"] or existing["title"]
        channel = channel or existing["channel"]
        duration_ms = duration_ms or existing["duration_ms"]
    if not title or duration_ms is None:
        try:
            info = yt.fetch_info(sid)
        except yt.YoutubeError as exc:
            if exc.code == "video_unavailable":
                raise invalid_request(exc.message, {"youtube_id": "indisponível"}) from exc
            raise youtube_api_error(exc) from exc
        if info.is_live:
            raise invalid_request(
                "Transmissões ao vivo não podem ser processadas.", {"youtube_id": "ao vivo"}
            )
        title, channel, duration_ms = (
            title or info.title,
            channel or info.channel,
            duration_ms or info.duration_ms,
        )
    threshold_ms = long_video_threshold_s(db, config) * 1000
    if duration_ms and duration_ms > threshold_ms and not body.confirm_long:
        raise ApiError(
            409,
            "long_video_confirmation_required",
            "Este vídeo é longo (mais de 15 min). Confirme para processar mesmo assim.",
            {"duration_ms": duration_ms},
        )

    # 3. música + job numa transação (revalida a corrida com outro POST)
    clean = clean_title(title or sid, channel)
    created = False
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM songs WHERE id = ?", (sid,)).fetchone()
        active = runner.active_job_for(conn, sid, "process")
        now = utcnow()
        if row is None:
            conn.execute(
                "INSERT INTO songs(id, title, artist, original_title, channel, duration_ms, status, added_by, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
                (sid, clean.track, clean.artist, title, channel, duration_ms, actor, now, now),
            )
        elif row["status"] != "ready" and active is None:
            conn.execute(
                "UPDATE songs SET status='queued', error_code=NULL, updated_at=? WHERE id=?", (now, sid)
            )
        if active is None and (row is None or row["status"] != "ready"):
            job_id = runner.insert_job(
                conn, "process", sid, actor, {"lyrics_mode": body.lyrics_mode} if body.lyrics_mode else None
            )
            created = True
        else:
            job_id = active["id"] if active is not None else None
    if created:
        runner.notify_created(job_id)
        runner.publish_song(sid, "updated")
    item = _enqueue(request, sid, body.singer, actor) if body.enqueue else None
    conn = db.connect()
    try:
        song = song_payload(conn, sid, actor)
        job = runner.job_dict(job_id, conn) if job_id else None
    finally:
        conn.close()
    return JSONResponse({"job": job, "song": song, "queue_item": item}, status_code=201 if created else 200)


@router.get("/api/jobs", dependencies=[scope("host")])
def list_jobs(
    request: Request, status: str = Query(default="active", pattern="^(active|recent|all)$")
) -> dict:
    return {"jobs": _runner(request).list_jobs(status)}


@router.get("/api/jobs/{job_id}", dependencies=[scope("guest-safe")])
def get_job(job_id: JobId, request: Request) -> dict:
    job = _runner(request).job_dict(job_id)
    if job is None:
        raise job_not_found(job_id)
    return job


@router.post("/api/jobs/{job_id}/retry", dependencies=[scope("host")])
def retry_job(job_id: JobId, request: Request) -> dict:
    try:
        job = _runner(request).retry(job_id)
    except ValueError as exc:
        raise ApiError(
            409,
            "job_not_retryable",
            "Só é possível tentar de novo um processamento com erro ou cancelado.",
            {"job_id": job_id},
        ) from exc
    if job is None:
        raise job_not_found(job_id)
    return job


@router.delete("/api/jobs/{job_id}", status_code=204, dependencies=[scope("host")])
def delete_job(job_id: JobId, request: Request) -> Response:
    if not _runner(request).cancel_or_remove(job_id):
        raise job_not_found(job_id)
    return Response(status_code=204)
