"""Fontes secundárias de letra em texto puro (ADR-0005): Vagalume e lyrics.ovh, consultadas depois do LRCLIB.

O LRCLIB continua em `karaoke/lrclib.py`. A orquestração (ordem, disparo, dedupe) fica em `chain.search_lyrics`.
Dono: backend-engineer (T-002, ciclo 20261020-2304-karaoke).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx


@dataclass(frozen=True)
class PlainLyrics:
    provider: str  # "vagalume" | "lyricsovh"
    provider_id: str  # "vagalume:<art>/<mus>" | "vagalume:api:<id>" | "lyricsovh:<deezer_id>"
    track_name: str  # nome que a FONTE devolveu
    artist_name: str
    duration_ms: int | None
    text_lines: list[str] = field(default_factory=list)


class ProviderNetworkError(Exception):
    """Falha de rede de um provedor: `offline` (sem conexão) ou `timeout`."""

    def __init__(self, kind: Literal["offline", "timeout"], message: str = ""):
        super().__init__(message or kind)
        self.kind = kind


class Provider(Protocol):
    name: str

    def search(self, track: str, artist: str | None) -> list[PlainLyrics]: ...

    def close(self) -> None: ...


def network_error(exc: httpx.HTTPError) -> ProviderNetworkError:
    """httpx → ProviderNetworkError (ADR-0005 §5): timeout → `timeout`; conexão/outros → `offline`."""
    kind: Literal["offline", "timeout"] = "timeout" if isinstance(exc, httpx.TimeoutException) else "offline"
    return ProviderNetworkError(kind, type(exc).__name__)


_BRACKET_LINE = re.compile(r"^\[[^\]]*\]$")


def clean_text(text: str) -> list[str]:
    """Limpeza comum (ADR-0005 §1.1): sem 1ª linha "Paroles de la chanson…", sem linhas inteiras entre colchetes;
    o resto (vazias, tags LRC, 500 chars) fica com `builder.text_lines_of`."""
    from karaoke.lyrics.builder import text_lines_of

    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    first = next((i for i, ln in enumerate(raw) if ln.strip()), None)
    if first is not None and raw[first].strip().startswith("Paroles de la chanson"):
        raw = raw[first + 1 :]
    kept = [ln for ln in raw if not _BRACKET_LINE.match(ln.strip())]
    return text_lines_of("\n".join(kept))


def build_providers(settings: dict[str, Any], transport: httpx.BaseTransport | None = None) -> list[Provider]:
    """`[]` com `extra_lyrics_sources` desligado; senão Vagalume (API se houver chave) e lyrics.ovh, nesta ordem."""
    if not settings.get("extra_lyrics_sources", True):
        return []
    from karaoke.lyrics_providers.lyricsovh import LyricsOvhProvider
    from karaoke.lyrics_providers.vagalume import VagalumeProvider

    vagalume = VagalumeProvider(settings.get("vagalume_api_key"), transport=transport)
    try:
        return [vagalume, LyricsOvhProvider(transport=transport)]
    except Exception:
        vagalume.close()
        raise
