"""Cadeia única de busca de letra (ADR-0005 §1/§3/§4): LRCLIB → Vagalume → lyrics.ovh.

- `exhaustive=False` (pipeline): fontes extras só se nenhum registro LRCLIB tiver duração aceitável; para no 1º
  provedor com resultado novo; LRCLIB sem conexão (não "fora do ar") ou provedor `offline` → os seguintes `skipped`.
- `exhaustive=True` (busca manual): LRCLIB e todos os provedores, cada um independente.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from karaoke import lrclib
from karaoke.lyrics.titleclean import normalize, strip_noise
from karaoke.lyrics_providers import PlainLyrics, Provider, ProviderNetworkError

log = logging.getLogger("karaoke.lyrics_providers")


@dataclass
class ChainResult:
    records: list[lrclib.LrcRecord] = field(default_factory=list)
    extra: list[PlainLyrics] = field(default_factory=list)
    status: dict[str, str] = field(default_factory=dict)
    lrclib_error: lrclib.LrclibNetworkError | None = None

    def all_network_failed(self) -> bool:
        """Todas as fontes consultadas falharam por rede (base do 502 da busca manual — AC-23.3)."""
        tried = [s for s in self.status.values() if s != "skipped"]
        return bool(tried) and all(s in ("offline", "timeout") for s in tried)


def _words_in(a: str, b: str) -> bool:
    return bool(a) and bool(b) and f" {a} " in f" {b} "


def match(cand_track: str, cand_artist: str, track: str, artist: str | None) -> bool:
    """Mesma faixa (sem ruído, normalizada) e, se `artist` informado, mesmo artista ou um contido no outro como
    sequência de palavras inteiras (ADR-0005 §3)."""
    nt = normalize(strip_noise(track))
    if not nt or normalize(strip_noise(cand_track)) != nt:
        return False
    if not artist:
        return True
    na, nc = normalize(artist), normalize(cand_artist)
    return na == nc or _words_in(na, nc) or _words_in(nc, na)


def fingerprint(lines: Iterable[str]) -> str:
    """Impressão do começo do texto (3 primeiras linhas, normalizadas) para deduplicar entre fontes."""
    return normalize(" ".join(list(lines)[:3]))


def _record_print(rec: lrclib.LrcRecord) -> str:
    from karaoke.lyrics.builder import text_lines_of

    return fingerprint(text_lines_of(rec.synced_lyrics or rec.plain_lyrics or ""))


def plain_candidate(p: PlainLyrics) -> dict[str, Any]:
    """PlainLyrics → candidata `online_plain` (mesmo formato do só-texto do LRCLIB, com `meta.provider`)."""
    return {
        "source": "online_plain",
        "timing": "none",
        "lines": [],
        "text_lines": list(p.text_lines),
        "language": None,
        "meta": {
            "track_name": p.track_name,
            "artist_name": p.artist_name,
            "album_name": None,
            "provider_id": p.provider_id,
            "provider": p.provider,
            "duration_ms": p.duration_ms,
            "model": None,
        },
    }


def close_all(providers: Iterable[Provider]) -> None:
    for p in providers:
        with contextlib.suppress(Exception):
            p.close()


def search_lyrics(
    client: lrclib.LrclibClient,
    providers: list[Provider],
    track: str,
    artist: str | None,
    duration_ms: int | None,
    raw_title: str | None,
    *,
    exhaustive: bool,
    known: set[Any] | None = None,
    known_prints: set[str] | None = None,
    check: Callable[[], None] | None = None,
) -> ChainResult:
    """Consulta as fontes conforme a regra de disparo. `known`/`known_prints` = `provider_id`s e impressões de texto
    já gravados (as extras repetidas são descartadas; os registros LRCLIB ficam para o chamador filtrar)."""
    res = ChainResult()
    try:
        res.records = lrclib.find_candidates(client, track, artist, duration_ms, raw_title)
        res.status["lrclib"] = "ok" if res.records else "empty"
    except lrclib.LrclibUnavailableError as exc:
        res.lrclib_error = exc
        res.status["lrclib"] = "timeout"
        log.info("LRCLIB fora do ar: %s", type(exc).__name__)
    except lrclib.LrclibNetworkError as exc:
        res.lrclib_error = exc
        res.status["lrclib"] = "offline"
        log.info("LRCLIB sem conexão: %s", type(exc).__name__)
    if check:
        check()

    skip_all = False
    if not exhaustive:
        offline = res.lrclib_error is not None and not isinstance(
            res.lrclib_error, lrclib.LrclibUnavailableError
        )
        skip_all = offline or any(lrclib.acceptable(r, duration_ms) for r in res.records)

    seen_ids: set[Any] = set(known or ())
    seen_prints: set[str] = {p for p in (known_prints or ()) if p}
    seen_prints |= {fp for r in res.records if (fp := _record_print(r))}
    for prov in providers:
        if skip_all:
            res.status[prov.name] = "skipped"
            continue
        try:
            found = prov.search(track, artist)
        except ProviderNetworkError as exc:
            res.status[prov.name] = exc.kind
            log.info("%s: %s", prov.name, exc.kind)
            if exc.kind == "offline" and not exhaustive:
                skip_all = True
            continue
        except Exception as exc:  # noqa: BLE001 — provedor nunca derruba a busca/o job (AC-22.4)
            res.status[prov.name] = "error"
            log.warning("%s: erro inesperado (%s)", prov.name, type(exc).__name__)
            continue
        finally:
            if check:
                check()
        res.status[prov.name] = "ok" if found else "empty"
        new = []
        for p in found:
            fp = fingerprint(p.text_lines)
            if not p.text_lines or p.provider_id in seen_ids or (fp and fp in seen_prints):
                continue
            seen_ids.add(p.provider_id)
            seen_prints.add(fp)
            new.append(p)
        res.extra += new
        if new and not exhaustive:
            skip_all = True  # pipeline: para no 1º provedor com resultado
    return res
