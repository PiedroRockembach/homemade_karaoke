"""SSE `GET /api/events` — contrato §10. Dono: backend-engineer (T-005).

Ao conectar: `retry`, depois `hello` com o snapshot completo (player, queue, jobs ativos, settings, system) para o
ator da conexão; em seguida os eventos incrementais do EventBus e `: ping` a cada 15 s. Conexões `role=stage` contam
em `stage.connected` (o player republica `player.state` quando o número muda, em até ~250 ms).

Ganchos opcionais para o T-006 (lidos de `app.state`, se existirem):
- `jobs_snapshot(actor) -> list[Job]` — jobs ativos no formato do contrato §5 (senão usa `job_to_dict` abaixo);
- `model_download` — `{model_id, progress}` durante download de modelo (senão `null`).
"""

from __future__ import annotations

import logging
import secrets
import sqlite3
import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from karaoke.api.settings import read_settings
from karaoke.deps import get_bus
from karaoke.events import ROLES, EventBus, sse_stream
from karaoke.security import Principal, current_principal, scope
from karaoke.services.library import ACTIVE_JOB_STAGES

log = logging.getLogger("karaoke")

router = APIRouter(tags=["events"], dependencies=[scope("guest-safe")])

_ACTIVE_SQL = ",".join(f"'{s}'" for s in ACTIVE_JOB_STAGES)
_probe_lock = threading.Lock()
_probe_started = False


def job_to_dict(row: sqlite3.Row, queue_position: int | None) -> dict[str, Any]:
    """Objeto Job (contrato §5) a partir de `jobs` + `songs` (fallback se o T-006 não expuser `jobs_snapshot`)."""
    error = None
    if row["error_code"]:
        error = {"code": row["error_code"], "message": row["error_message"] or "Erro no processamento."}
    return {
        "id": row["id"],
        "kind": row["kind"],
        "song_id": row["song_id"],
        "song": {
            "title": row["title"],
            "artist": row["artist"],
            "thumbnail": f"/api/thumbs/{row['song_id']}",
        },
        "stage": row["stage"],
        "stage_progress": row["stage_progress"],
        "progress": row["progress"],
        "queue_position": queue_position,
        "device": row["device"],
        "lyrics_source": row["lyrics_source"],
        "error": error,
        "attempts": row["attempts"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def active_jobs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT j.*, s.title, s.artist FROM jobs j JOIN songs s ON s.id = j.song_id "  # noqa: S608
        f"WHERE j.stage IN ({_ACTIVE_SQL}) ORDER BY j.created_at, j.rowid"
    ).fetchall()
    running = [r for r in rows if r["stage"] != "queued"]
    queued = [r for r in rows if r["stage"] == "queued"]
    out = [job_to_dict(r, 0) for r in running]
    base = 1 if running else 0
    out += [job_to_dict(r, base + i) for i, r in enumerate(queued)]
    return out


def _start_gpu_probe(app: FastAPI) -> None:
    """Sonda a GPU uma vez em background (subprocesso) e publica `system.updated` quando terminar."""
    global _probe_started
    with _probe_lock:
        if _probe_started:
            return
        _probe_started = True

    def run() -> None:
        from karaoke import system

        try:
            device, warning = system.resolve_device(app.state.config, system.probe_gpu())
            app.state.bus.publish(
                "system.updated",
                {
                    "device": device,
                    "gpu_warning": warning,
                    "model_download": getattr(app.state, "model_download", None),
                },
            )
        except Exception:  # noqa: BLE001
            log.exception("falha ao sondar a GPU")

    threading.Thread(target=run, name="gpu-probe", daemon=True).start()


def system_snapshot(app: FastAPI) -> dict[str, Any]:
    from karaoke import system

    config = app.state.config
    model_download = getattr(app.state, "model_download", None)
    if config.device == "cpu":
        return {"device": "cpu", "gpu_warning": None, "model_download": model_download}
    if system.probe_gpu.cache_info().currsize:
        device, warning = system.resolve_device(config, system.probe_gpu())
        return {"device": device, "gpu_warning": warning, "model_download": model_download}
    _start_gpu_probe(app)  # resultado chega por `system.updated`
    return {
        "device": "cuda" if config.device == "cuda" else None,
        "gpu_warning": None,
        "model_download": model_download,
    }


def hello_snapshot(app: FastAPI, client_id: str, actor: str) -> dict[str, Any]:
    conn = app.state.db.connect()
    try:
        jobs_hook = getattr(app.state, "jobs_snapshot", None)
        jobs = jobs_hook(actor) if callable(jobs_hook) else active_jobs(conn)
        return {
            "server_time": int(time.time() * 1000),
            "client_id": client_id,
            "player": app.state.player.state_conn(conn, actor),
            "queue": app.state.queue.snapshot_conn(conn, actor),
            "jobs": jobs,
            "settings": read_settings(conn, app.state.config),
            "system": system_snapshot(app),
        }
    finally:
        conn.close()


@router.get("/api/events")
async def events(
    request: Request,
    client_id: str | None = Query(default=None, pattern=r"^c_[A-Za-z0-9_-]{1,64}$"),
    role: str = Query(default="control", pattern="^(" + "|".join(ROLES) + ")$"),
    bus: EventBus = Depends(get_bus),
    principal: Principal = Depends(current_principal),
) -> StreamingResponse:
    cid = client_id or f"c_{secrets.token_urlsafe(6)}"
    sub = bus.subscribe(cid, role, principal)
    try:
        # o snapshot lê o banco e pega o lock de estado: fora do event loop, para não congelar os outros streams
        snapshot = await run_in_threadpool(hello_snapshot, request.app, cid, principal.id)
        bus.send_first(sub, "hello", snapshot)
    except BaseException:
        bus.unsubscribe(sub)
        raise
    return StreamingResponse(
        sse_stream(bus, sub),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
