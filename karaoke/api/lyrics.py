"""Letras — contrato §7 (todas `host`). Dono: backend-engineer (T-006; v0.2: T-001 do ciclo 20261004-1742)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from karaoke.deps import SongId
from karaoke.security import current_actor, scope
from karaoke.services.lyrics import MAX_TIMING_LINES, LyricsService, TimingLine

router = APIRouter(tags=["lyrics"], dependencies=[scope("host")])

CANDIDATE_PATTERN = r"^(lrclib|whisper|manual|aligned|online_plain):[0-9]{1,12}$"


class SelectBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    candidate_id: str = Field(pattern=CANDIDATE_PATTERN)


class ManualBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    lrc: str = Field(max_length=200_000)


class SearchBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    track_name: str | None = Field(default=None, max_length=300)
    artist_name: str | None = Field(default=None, max_length=300)


class TranscribeBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}$")


class AlignBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    candidate_id: str | None = Field(default=None, pattern=CANDIDATE_PATTERN)
    language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}$")


class ManualTimingBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    candidate_id: str | None = Field(default=None, pattern=CANDIDATE_PATTERN)
    lines: list[TimingLine] = Field(min_length=1, max_length=MAX_TIMING_LINES)


class DetectOffsetBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    candidate_id: str | None = Field(default=None, pattern=CANDIDATE_PATTERN)


def _svc(request: Request) -> LyricsService:
    svc = getattr(request.app.state, "lyrics", None)
    if svc is None:
        svc = request.app.state.lyrics = LyricsService(request.app)
    return svc


@router.get("/api/songs/{song_id}/lyrics")
def get_lyrics(song_id: SongId, request: Request) -> dict:
    return _svc(request).document(song_id)


@router.get("/api/songs/{song_id}/lyrics/candidates")
def list_candidates(song_id: SongId, request: Request) -> dict:
    return _svc(request).candidates(song_id)


@router.put("/api/songs/{song_id}/lyrics/selected")
def select_candidate(song_id: SongId, body: SelectBody, request: Request) -> dict:
    return _svc(request).select(song_id, body.candidate_id)


@router.post("/api/songs/{song_id}/lyrics/manual", status_code=201)
def manual_lrc(
    song_id: SongId, body: ManualBody, request: Request, actor: str = Depends(current_actor)
) -> JSONResponse:
    result, plain = _svc(request).manual(song_id, body.lrc, actor)
    return JSONResponse(result, status_code=202 if plain else 201)


@router.post("/api/songs/{song_id}/lyrics/search")
def search_lrclib(song_id: SongId, request: Request, body: SearchBody | None = None) -> dict:
    body = body or SearchBody()
    return _svc(request).search(song_id, body.track_name, body.artist_name)


@router.post("/api/songs/{song_id}/lyrics/transcribe", status_code=202)
def transcribe(
    song_id: SongId, request: Request, body: TranscribeBody | None = None, actor: str = Depends(current_actor)
) -> dict:
    body = body or TranscribeBody()
    return _svc(request).transcribe(song_id, body.language, actor)


@router.post("/api/songs/{song_id}/lyrics/align", status_code=202)
def align_by_voice(
    song_id: SongId, request: Request, body: AlignBody | None = None, actor: str = Depends(current_actor)
) -> dict:
    body = body or AlignBody()
    return _svc(request).align(song_id, body.candidate_id, body.language, actor)


@router.put("/api/songs/{song_id}/lyrics/manual-timing")
def manual_timing(song_id: SongId, body: ManualTimingBody, request: Request) -> JSONResponse:
    doc, created = _svc(request).manual_timing(song_id, body.candidate_id, body.lines)
    return JSONResponse(doc, status_code=201 if created else 200)


@router.post("/api/songs/{song_id}/lyrics/detect-offset")
def detect_offset(
    song_id: SongId,
    request: Request,
    body: DetectOffsetBody | None = None,
    actor: str = Depends(current_actor),
) -> JSONResponse:
    body = body or DetectOffsetBody()
    result, queued = _svc(request).detect_offset(song_id, body.candidate_id, actor)
    return JSONResponse(result, status_code=202 if queued else 200)
