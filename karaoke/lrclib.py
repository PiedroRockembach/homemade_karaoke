"""Cliente LRCLIB (httpx): /api/get e /api/search, ranking por diferença de duração (≤ 3 s).

Registros com `syncedLyrics` (letra sincronizada) **ou** só com `plainLyrics` (v0.2/CR-001: texto oficial para o
alinhamento pela voz) são aproveitados; sem nenhum dos dois, descartados. 429/5xx e timeouts são repetidos com backoff curto;
se persistirem, `LrclibUnavailableError` (LRCLIB fora do ar). Sem conexão: `LrclibNetworkError` (base das duas);
o pipeline trata isso como "sem letra do LRCLIB" e cai no Whisper (ARCHITECTURE §3).

Dono: backend-engineer (T-006).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from karaoke import __version__
from karaoke.lyrics.titleclean import normalize, strip_noise

log = logging.getLogger("karaoke.lrclib")

BASE_URL = "https://lrclib.net"
USER_AGENT = f"karaoke-local/{__version__} (app local de karaoke, uso pessoal)"
ACCEPT_DIFF_MS = 3000
TIMEOUT_S = 10.0
MAX_CANDIDATES = 8


RETRY_BACKOFF_S = (0.5, 1.5)  # esperas antes da 2ª e da 3ª tentativa (429/5xx)
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_TIMEOUT_ATTEMPTS = 2  # timeouts custam TIMEOUT_S cada: no máximo 1 nova tentativa
MAX_RETRY_AFTER_S = 3.0


class LrclibNetworkError(Exception):
    """Sem internet / LRCLIB inacessível."""


class LrclibUnavailableError(LrclibNetworkError):
    """A internet funciona, mas o LRCLIB respondeu erro temporário (429/5xx) ou não respondeu a tempo, mesmo após
    as novas tentativas. Subclasse de `LrclibNetworkError`: quem só trata "sem LRCLIB" continua funcionando."""


@dataclass
class LrcRecord:
    id: int
    track_name: str
    artist_name: str
    album_name: str | None
    duration_ms: int | None
    synced_lyrics: str | None
    instrumental: bool = False
    plain_lyrics: str | None = None

    @property
    def synced(self) -> bool:
        return bool(self.synced_lyrics)

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> LrcRecord | None:
        def text(key: str) -> str | None:
            v = d.get(key)
            return v if isinstance(v, str) and v.strip() else None

        synced, plain = text("syncedLyrics"), text("plainLyrics")
        if (not synced and not plain) or d.get("id") is None:
            return None
        dur = d.get("duration")
        return cls(
            id=int(d["id"]),
            track_name=d.get("trackName") or d.get("name") or "",
            artist_name=d.get("artistName") or "",
            album_name=d.get("albumName"),
            duration_ms=round(float(dur) * 1000) if dur else None,
            synced_lyrics=synced,
            instrumental=bool(d.get("instrumental")),
            plain_lyrics=plain,
        )

    def diff_ms(self, duration_ms: int | None) -> int | None:
        if self.duration_ms is None or not duration_ms:
            return None
        return self.duration_ms - duration_ms


class LrclibClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._sleep = sleep
        self._client = httpx.Client(
            base_url=base_url,
            headers={"User-Agent": USER_AGENT, "Lrclib-Client": USER_AGENT},
            timeout=TIMEOUT_S,
            transport=transport,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        """GET com novas tentativas (backoff curto) para 429/5xx e timeouts. Falha de conexão (sem internet) não
        é repetida: offline a resposta tem de ser imediata."""
        query = {k: v for k, v in params.items() if v not in (None, "")}
        attempt = 0
        timeouts = 0
        while True:
            attempt += 1
            retry_in: float | None = None
            try:
                r = self._client.get(path, params=query)
            except httpx.TimeoutException as exc:
                timeouts += 1
                if timeouts >= MAX_TIMEOUT_ATTEMPTS or attempt > len(RETRY_BACKOFF_S):
                    raise LrclibUnavailableError(f"LRCLIB não respondeu a tempo: {exc}") from exc
                retry_in = RETRY_BACKOFF_S[attempt - 1]
                log.info("LRCLIB %s: timeout; nova tentativa em %.1fs", path, retry_in)
            except httpx.ConnectError as exc:
                raise LrclibNetworkError(str(exc)) from exc
            except httpx.HTTPError as exc:  # conexão caiu no meio, protocolo etc.
                if attempt > len(RETRY_BACKOFF_S):
                    raise LrclibNetworkError(str(exc)) from exc
                retry_in = RETRY_BACKOFF_S[attempt - 1]
                log.info("LRCLIB %s: %s; nova tentativa em %.1fs", path, exc, retry_in)
            else:
                if r.status_code in RETRY_STATUS:
                    if attempt > len(RETRY_BACKOFF_S):
                        raise LrclibUnavailableError(f"LRCLIB respondeu {r.status_code}")
                    retry_in = max(RETRY_BACKOFF_S[attempt - 1], _retry_after(r))
                    log.info("LRCLIB %s -> %s; nova tentativa em %.1fs", path, r.status_code, retry_in)
                else:
                    return _decode(r, path, params)
            self._sleep(retry_in)

    def get(self, track: str, artist: str | None, duration_ms: int | None) -> LrcRecord | None:
        if not artist or not duration_ms:
            return None
        d = self._get(
            "/api/get",
            {"track_name": track, "artist_name": artist, "duration": round(duration_ms / 1000)},
        )
        return LrcRecord.from_api(d) if isinstance(d, dict) else None

    def search(self, track: str, artist: str | None = None, q: str | None = None) -> list[LrcRecord]:
        params: dict[str, Any] = {"q": q} if q else {"track_name": track, "artist_name": artist}
        data = self._get("/api/search", params)
        if not isinstance(data, list):
            return []
        recs = [LrcRecord.from_api(d) for d in data if isinstance(d, dict)]
        return [r for r in recs if r is not None]


def _retry_after(r: httpx.Response) -> float:
    try:
        return min(MAX_RETRY_AFTER_S, max(0.0, float(r.headers.get("retry-after", 0))))
    except ValueError:
        return 0.0


def _decode(r: httpx.Response, path: str, params: dict[str, Any]) -> Any:
    if r.status_code == 404:
        return None
    if r.status_code >= 500:
        raise LrclibUnavailableError(f"LRCLIB respondeu {r.status_code}")
    if r.status_code >= 400:
        log.warning("LRCLIB %s %s -> %s", path, params, r.status_code)
        return None
    try:
        return r.json()
    except ValueError:
        return None


def rank(
    records: list[LrcRecord], duration_ms: int | None, track: str, artist: str | None
) -> list[LrcRecord]:
    """Remove duplicatas e ordena por |diferença de duração| (desconhecida vai pro fim), preferindo nome igual."""
    seen: set[int] = set()
    uniq = []
    for r in records:
        if r.id in seen or r.instrumental:
            continue
        seen.add(r.id)
        uniq.append(r)
    nt, na = normalize(track), normalize(artist)

    def key(r: LrcRecord) -> tuple[int, int, int, int]:
        diff = r.diff_ms(duration_ms)
        name_penalty = (0 if normalize(r.track_name) == nt else 1) + (
            0 if not na or normalize(r.artist_name) == na else 1
        )
        # duração aceitável primeiro; dentro dela, com tempos antes de só texto
        ok = diff is not None and abs(diff) <= ACCEPT_DIFF_MS
        return (
            0 if ok else 1,
            0 if r.synced else 1,
            abs(diff) if diff is not None else 10**9,
            name_penalty * 10**6 + r.id % 10**6,
        )

    return sorted(uniq, key=key)


def good(records: list[LrcRecord], duration_ms: int | None, track: str) -> bool:
    """Resultado "bom" o bastante para parar de buscar: uma letra **com tempos** de duração aceitável (ou, sem
    duração conhecida, com o nome da faixa igual)."""
    nt = normalize(track)
    for r in records:
        if not r.synced or r.instrumental:
            continue
        if duration_ms and acceptable(r, duration_ms):
            return True
        if not duration_ms and normalize(r.track_name) == nt:
            return True
    return False


def find_candidates(
    client: LrclibClient,
    track: str,
    artist: str | None,
    duration_ms: int | None,
    raw_title: str | None = None,
) -> list[LrcRecord]:
    """Cadeia de buscas (CR-001), parando no primeiro resultado `good`:
    1. `/api/get` (título limpo + artista + duração) e `/api/search?track_name=&artist_name=` — sempre os dois
       (a lista de candidatas alternativas vem da busca por campos);
    2. `/api/search?q=<artista> <faixa>` (texto livre);
    3. `/api/search?q=<título do vídeo sem ruído>` (quando diferente do anterior).
    Registros só com texto entram na lista (fonte de texto para alinhar). Retorna candidatas ranqueadas."""
    found: list[LrcRecord] = []
    exact = client.get(track, artist, duration_ms)
    if exact:
        found.append(exact)
    found += client.search(track, artist)
    queries = [f"{artist or ''} {track}".strip()]
    if raw_title:
        q2 = strip_noise(raw_title)
        if q2 and normalize(q2) != normalize(queries[0]):
            queries.append(q2)
    for q in queries:
        if good(found, duration_ms, track):
            break
        found += client.search(track, q=q)
    return rank(found, duration_ms, track, artist)[:MAX_CANDIDATES]


def acceptable(record: LrcRecord | None, duration_ms: int | None) -> bool:
    """Aceita automaticamente só se |duração LRCLIB − vídeo| ≤ 3 s."""
    if record is None:
        return False
    diff = record.diff_ms(duration_ms)
    return diff is not None and abs(diff) <= ACCEPT_DIFF_MS
