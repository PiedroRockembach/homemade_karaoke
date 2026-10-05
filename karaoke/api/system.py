"""Saúde, sistema (GPU/modelos/ferramentas) e pré-download de modelos — contrato §3. Dono: backend-engineer (T-006)."""

from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from karaoke import __version__
from karaoke import system as sysinfo
from karaoke.config import Config
from karaoke.deps import get_config
from karaoke.errors import ApiError
from karaoke.jobs import models
from karaoke.security import scope

log = logging.getLogger("karaoke")
router = APIRouter(tags=["system"])


@router.get("/api/health", dependencies=[scope("guest-safe")])
async def health() -> dict:
    return {"ok": True, "version": __version__}


@router.get("/api/system", dependencies=[scope("host")])
async def system(request: Request, config: Config = Depends(get_config)) -> dict:
    return await run_in_threadpool(sysinfo.collect, config, request.app.state.db)


@router.post("/api/system/models/{model_id}/download", status_code=202, dependencies=[scope("host")])
async def download_model(
    model_id: str, request: Request, config: Config = Depends(get_config)
) -> JSONResponse:
    if models.spec(model_id) is None:
        raise ApiError(404, "model_not_found", "Modelo desconhecido.", {"model_id": model_id})
    if models.is_downloaded(config, model_id):
        return JSONResponse({"model_id": model_id, "status": "downloaded"}, status_code=200)
    current = models.current_download()
    if current and current.get("model_id") == model_id:
        return JSONResponse({"model_id": model_id, "status": "downloading"}, status_code=202)
    bus = request.app.state.bus
    runner = getattr(request.app.state, "runner", None)

    def publish(force: bool = False) -> None:
        if runner is not None:
            runner.bus_system_update(force=force)
        else:
            bus.publish(
                "system.updated",
                {"device": None, "gpu_warning": None, "model_download": models.current_download()},
            )

    def work() -> None:
        try:
            models.download(config, model_id, on_progress=lambda _f: publish())
            bus.publish("toast", {"level": "info", "message": f"Modelo {model_id} baixado."})
        except models.ModelDownloadError as exc:
            log.warning("download do modelo %s falhou: %s", model_id, exc)
            bus.publish("toast", {"level": "error", "message": str(exc)})
        finally:
            publish(force=True)

    threading.Thread(target=work, name=f"model-download-{model_id}", daemon=True).start()
    return JSONResponse({"model_id": model_id, "status": "downloading"}, status_code=202)
