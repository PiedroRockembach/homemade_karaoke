"""Limite de tamanho do corpo da requisição, **antes** de qualquer leitura (review-1 H-1; api.md §0 `413`).

Middleware ASGI puro, instalado em `create_app` e, portanto, ativo nos dois servidores (loopback e LAN), que
compartilham o mesmo app:

- `Content-Length` acima do limite (ou inválido) → `413 payload_too_large` sem ler nada do corpo;
- sem `Content-Length` (chunked): os bytes são contados no `receive`; ao passar do limite a leitura é abortada, a
  resposta do app é descartada e sai o `413`.

Limites: o cliente loopback (host) tem um teto generoso, porque a colagem de LRC aceita até 200 000 caracteres
(até ~1,2 MB em JSON com escapes `\\uXXXX`) e o editor de sincronia manda até `MAX_TIMING_LINES` (2000) linhas
com palavras; 8 MiB cobre os dois com folga. Qualquer
outro cliente (listener da LAN) tem 16 KiB — o maior corpo legítimo do convidado é `POST /api/guest/session` ou
`POST /api/jobs`, bem abaixo de 1 KiB.
"""

from __future__ import annotations

import ipaddress
import json
import logging
from typing import Any

log = logging.getLogger("karaoke")

HOST_MAX_BODY = 8 * 1024 * 1024
LAN_MAX_BODY = 16 * 1024


class _BodyTooLarge(Exception):
    pass


def _is_loopback(scope: dict[str, Any]) -> bool:
    client = scope.get("client")
    if not client:
        return False
    try:
        return ipaddress.ip_address(client[0]).is_loopback
    except ValueError:
        return False


def limit_for(scope: dict[str, Any]) -> int:
    return HOST_MAX_BODY if _is_loopback(scope) else LAN_MAX_BODY


def _content_length(scope: dict[str, Any]) -> int | None:
    """`None` se ausente; `-1` se inválido ou repetido com valores diferentes."""
    values = {v.strip() for k, v in scope.get("headers", ()) if k.lower() == b"content-length"}
    if not values:
        return None
    if len(values) != 1:
        return -1
    raw = values.pop()
    if not raw.isdigit():
        return -1
    return int(raw)


def _body_413(limit: int) -> bytes:
    return json.dumps(
        {
            "error": {
                "code": "payload_too_large",
                "message": "A requisição é grande demais.",
                "details": {"max_bytes": limit},
            }
        }
    ).encode("utf-8")


class BodyLimitMiddleware:
    def __init__(self, app: Any):
        self.app = app

    async def _send_413(self, send: Any, limit: int) -> None:
        body = _body_413(limit)
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        limit = limit_for(scope)
        declared = _content_length(scope)
        if declared is not None and (declared < 0 or declared > limit):
            client = scope.get("client") or ("?", 0)
            log.warning("corpo recusado (%s bytes declarados, limite %s) de %s", declared, limit, client[0])
            await self._send_413(send, limit)
            return

        received = 0
        tripped = False
        started = False

        async def limited_receive() -> Any:
            nonlocal received, tripped
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    tripped = True
                    raise _BodyTooLarge
            return message

        async def guarded_send(message: Any) -> None:
            nonlocal started
            if tripped:
                return  # a resposta do app (400/500 pela exceção de leitura) é trocada pelo 413
            if message.get("type") == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not tripped:
                raise
        if tripped and not started:
            client = scope.get("client") or ("?", 0)
            log.warning("corpo recusado (mais de %s bytes, sem Content-Length) de %s", limit, client[0])
            await self._send_413(send, limit)
