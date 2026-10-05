"""Erros no formato do contrato (docs/contracts/api.md §0 "Formato de erro") e handlers do FastAPI."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("karaoke")


class ApiError(Exception):
    """Erro de API. Levante nos handlers/serviços; vira `{"error": {code, message, details}}`."""

    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def error_response(
    status: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(error_body(code, message, details), status_code=status, headers=headers)


# ---- atalhos usados por várias rotas ----------------------------------------
def not_implemented(feature: str = "") -> ApiError:
    """Stub do esqueleto (T-004). Some quando a rota é implementada."""
    return ApiError(
        501, "not_implemented", "Ainda não implementado.", {"feature": feature} if feature else None
    )


def forbidden(reason: str) -> ApiError:
    return ApiError(403, "forbidden", "Acesso negado.", {"reason": reason})


def not_owner(resource_id: str | None = None) -> ApiError:
    return ApiError(
        403, "not_owner", "Só quem adicionou pode remover.", {"id": resource_id} if resource_id else None
    )


def invalid_request(message: str = "Requisição inválida.", fields: dict[str, str] | None = None) -> ApiError:
    return ApiError(400, "invalid_request", message, {"fields": fields} if fields else None)


_HTTP_CODES = {
    404: ("not_found", "Recurso não encontrado."),
    405: ("method_not_allowed", "Método não permitido."),
    400: ("invalid_request", "Requisição inválida."),
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields: dict[str, str] = {}
        for e in exc.errors():
            loc = ".".join(str(p) for p in e.get("loc", ()) if p not in ("body", "query", "path"))
            fields[loc or "_"] = e.get("msg", "inválido")
        return error_response(400, "invalid_request", "Requisição inválida.", {"fields": fields})

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code, message = _HTTP_CODES.get(exc.status_code, ("http_error", str(exc.detail)))
        return error_response(exc.status_code, code, message, headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("erro inesperado", exc_info=exc)
        return error_response(500, "internal_error", "Erro inesperado. Veja o log em data/logs/karaoke.log.")
