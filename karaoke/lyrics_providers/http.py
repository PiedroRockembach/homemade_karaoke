"""Cliente HTTP comum dos provedores extras (ADR-0005 §1.1): UA do app, timeouts curtos, sem nova tentativa."""

from __future__ import annotations

import logging

import httpx

from karaoke.lrclib import USER_AGENT

TIMEOUT = httpx.Timeout(5.0, connect=3.0)

# o httpx loga a URL de cada requisição em INFO — a da API do Vagalume leva a chave (ADR-0005 §5)
logging.getLogger("httpx").setLevel(logging.WARNING)


def make_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
        transport=transport,
        follow_redirects=True,
    )
