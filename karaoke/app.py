"""`create_app()`: monta o FastAPI com autorização global, routers, páginas, estáticos, CSP e erros.

Dono: architect. **Engenheiros não editam este arquivo**: cada router em `karaoke/api/*.py` já está registrado, e
inicialização/encerramento de serviços entram por funções `startup(app)` / `shutdown(app)` (sync ou async) definidas
nos módulos listados em `LIFECYCLE_MODULES`.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import import_module
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, Response

from karaoke import __version__
from karaoke.config import WEB_DIR, Config, load_config
from karaoke.db import Database
from karaoke.errors import ApiError, install_error_handlers
from karaoke.events import EventBus
from karaoke.security import authorize, scope

log = logging.getLogger("karaoke")

CSP = (
    "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
    "style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)

ROUTER_MODULES = (
    "system",
    "settings",
    "search",
    "jobs",
    "songs",
    "lyrics",
    "queue",
    "player",
    "media",
    "events",
)

# Ordem de startup; shutdown na ordem inversa. Cada módulo pode definir startup(app) e/ou shutdown(app).
LIFECYCLE_MODULES = (
    "karaoke.services.library",
    "karaoke.services.lyrics",
    "karaoke.services.queue",
    "karaoke.services.player",
    "karaoke.jobs.runner",
)

STATIC_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".woff2": "font/woff2",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json",
    ".html": "text/html; charset=utf-8",
}


async def _call_hook(module_name: str, hook: str, app: FastAPI) -> None:
    fn = getattr(import_module(module_name), hook, None)
    if fn is None:
        return
    result = fn(app)
    if inspect.isawaitable(result):
        await result


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    config: Config = app.state.config
    config.ensure_dirs()
    app.state.db.migrate()
    app.state.bus.bind_loop()
    started: list[str] = []
    try:
        for name in LIFECYCLE_MODULES:
            await _call_hook(name, "startup", app)
            started.append(name)
        yield
    finally:
        app.state.bus.close_all()
        for name in reversed(started):
            try:
                await _call_hook(name, "shutdown", app)
            except Exception:
                log.exception("erro no shutdown de %s", name)


def _web_file(rel: str) -> Path:
    """Resolve um caminho dentro de web/ sem permitir escapar do diretório."""
    root = WEB_DIR.resolve()
    target = (root / rel).resolve()
    if root not in target.parents or not target.is_file():
        raise ApiError(404, "not_found", "Arquivo não encontrado.")
    return target


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()
    app = FastAPI(
        title="Karaokê local",
        version=__version__,
        lifespan=_lifespan,
        dependencies=[Depends(authorize)],  # ponto único de autorização (ADR-0002)
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.config = config
    app.state.db = Database(config.db_path)
    app.state.bus = EventBus()
    app.state.rate_limiter = None  # NoopRateLimiter na v1

    install_error_handlers(app)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):  # noqa: ANN001, ANN202
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    # ---- páginas e estáticos -------------------------------------------------------
    def _page(name: str) -> FileResponse:
        return FileResponse(
            _web_file(name), media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"}
        )

    @app.get("/", include_in_schema=False, dependencies=[scope("host")])
    async def control_page() -> Response:
        return _page("index.html")

    @app.get("/stage", include_in_schema=False, dependencies=[scope("host")])
    async def stage_page() -> Response:
        return _page("stage.html")

    @app.get("/static/{path:path}", include_in_schema=False, dependencies=[scope("guest-safe")])
    async def static_file(path: str) -> Response:
        target = _web_file(path)
        return FileResponse(
            target, media_type=STATIC_TYPES.get(target.suffix.lower()), headers={"Cache-Control": "no-cache"}
        )

    # ---- routers da API --------------------------------------------------------------
    for name in ROUTER_MODULES:
        app.include_router(import_module(f"karaoke.api.{name}").router)

    return app
