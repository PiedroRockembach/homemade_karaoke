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

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers


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


# ---- v0.3: modo convidado (api.md §0 "Formato de erro", §12; ADR-0003) ----------------------------
def guest_session_required() -> ApiError:
    return ApiError(
        401, "guest_session_required", "Sessão encerrada. Escaneie o QR da TV para entrar de novo."
    )


def guest_invite_invalid() -> ApiError:
    return ApiError(401, "guest_invite_invalid", "Este QR não vale mais — escaneie o QR da TV de novo.")


def rate_limited(retry_after_s: int, reason: str) -> ApiError:
    """`429 rate_limited` com `details.retry_after_s`/`reason` e header `Retry-After` iguais (inteiro ≥ 1)."""
    retry = max(1, int(retry_after_s))
    return ApiError(
        429,
        "rate_limited",
        "Calma! Tente em alguns segundos.",
        {"retry_after_s": retry, "reason": reason},
        headers={"Retry-After": str(retry)},
    )


def guest_mode_off() -> ApiError:
    return ApiError(409, "guest_mode_off", "O modo convidado está desligado.")


def lan_unavailable() -> ApiError:
    return ApiError(
        409,
        "lan_unavailable",
        "Nenhuma rede local encontrada. Conecte o PC ao Wi-Fi ou cabo da sua rede.",
    )


def lan_bind_failed(address: str, port: int, reason: str) -> ApiError:
    return ApiError(
        409,
        "lan_bind_failed",
        f"Não foi possível abrir a porta {port} em {address}.",
        {"address": address, "port": port, "reason": reason},
    )


def long_video_not_allowed(duration_ms: int, max_ms: int) -> ApiError:
    return ApiError(
        409,
        "long_video_not_allowed",
        f"Vídeos com mais de {max_ms // 60000} minutos não podem ser pedidos por convidados.",
        {"duration_ms": duration_ms, "max_ms": max_ms},
    )


def guest_quota_exceeded(quota: str, limit: int) -> ApiError:
    """`quota`: `pending_items` | `active_jobs`."""
    message = {
        "pending_items": f"Você já tem {limit} pedidos na fila. Espere um deles tocar.",
        "active_jobs": f"Você já tem {limit} músicas sendo preparadas. Espere uma delas ficar pronta.",
    }.get(quota, "Limite de pedidos atingido.")
    return ApiError(409, "guest_quota_exceeded", message, {"quota": quota, "limit": limit})


def queue_item_playing(item_id: str | None = None) -> ApiError:
    return ApiError(
        409,
        "queue_item_playing",
        "Essa música já está tocando e não pode ser removida.",
        {"id": item_id} if item_id else None,
    )


def song_queued_by_others(count: int, song_id: str | None = None) -> ApiError:
    details: dict[str, Any] = {"count": count}
    if song_id:
        details["song_id"] = song_id
    return ApiError(
        409,
        "song_queued_by_others",
        "Essa música está na fila de outras pessoas. Remova os pedidos antes de excluí-la.",
        details,
    )


# Código de erro **de job** (campo `job.error.code`, api.md §0): job de convidado cuja duração real passou de
# `options.max_duration_ms` (defesa em profundidade, ADR-0003 §5).
VIDEO_TOO_LONG = "video_too_long"


def video_too_long_message(max_ms: int) -> str:
    return f"O vídeo tem mais de {max_ms // 60000} minutos (limite para convidados)."


_HTTP_CODES = {
    404: ("not_found", "Recurso não encontrado."),
    405: ("method_not_allowed", "Método não permitido."),
    400: ("invalid_request", "Requisição inválida."),
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message, exc.details, headers=exc.headers)

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
