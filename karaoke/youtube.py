"""YouTube via yt-dlp (API Python, sem shell): busca ytsearchN com extract_flat, download de áudio com
progress_hooks, proxy/cache de thumbnails em data/cache/thumbs.

Dono: backend-engineer (T-006).
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("karaoke.youtube")

YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
THUMB_CACHE_MAX_BYTES = 200 * 1024 * 1024
THUMB_TIMEOUT_S = 10.0
THUMB_MAX_BYTES = 2 * 1024 * 1024
UPDATE_HINT = "Atualize o yt-dlp com `make update-ytdlp` e tente de novo."

_NETWORK_PATTERNS = re.compile(
    r"getaddrinfo|name or service not known|temporary failure in name resolution|network is unreachable|"
    r"no route to host|connection (?:refused|reset|aborted)|timed out|unable to download webpage|"
    r"failed to resolve|nodename nor servname|urlopen error|ssl:|remote end closed",
    re.IGNORECASE,
)
_UNAVAILABLE_PATTERNS = re.compile(
    r"video (?:is )?unavailable|private video|this video is not available|has been removed|"
    r"account associated with this video has been terminated|members-only|age[- ]restricted|"
    r"sign in to confirm your age|copyright",
    re.IGNORECASE,
)
_LIVE_PATTERNS = re.compile(r"live event|is live|premieres in|this live stream", re.IGNORECASE)


class YoutubeError(Exception):
    """Erro já classificado: `code` ∈ network_unavailable | youtube_error | video_unavailable | download_failed."""

    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


class DownloadCanceled(Exception):
    pass


def classify_error(exc: BaseException) -> YoutubeError:
    if isinstance(exc, YoutubeError):
        return exc
    text = str(exc)
    if _NETWORK_PATTERNS.search(text):
        return YoutubeError(
            "network_unavailable", "Sem conexão com a internet. Verifique a rede e tente de novo."
        )
    if _LIVE_PATTERNS.search(text):
        return YoutubeError("video_unavailable", "Transmissões ao vivo não podem ser processadas.")
    if _UNAVAILABLE_PATTERNS.search(text):
        return YoutubeError("video_unavailable", "Este vídeo não está disponível para download.")
    return YoutubeError("youtube_error", "Não foi possível acessar o YouTube.", UPDATE_HINT)


def _base_opts() -> dict[str, Any]:
    from karaoke.system import find_deno

    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 3,
        "logger": _YdlLogger(),
    }
    deno = find_deno()
    if deno:
        opts["js_runtimes"] = {"deno": {"path": deno}}
    return opts


class _YdlLogger:
    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.warning("yt-dlp: %s", msg)


# ---- busca --------------------------------------------------------------------------
@dataclass
class SearchResult:
    youtube_id: str
    title: str
    channel: str | None
    duration_ms: int | None
    is_live: bool

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.youtube_id}"


def _entry_to_result(e: dict[str, Any]) -> SearchResult | None:
    vid = e.get("id")
    if not isinstance(vid, str) or not YOUTUBE_ID.match(vid):
        return None  # canais/playlists nos resultados
    dur = e.get("duration")
    live = e.get("live_status") in ("is_live", "is_upcoming", "post_live") or bool(e.get("is_live"))
    return SearchResult(
        youtube_id=vid,
        title=e.get("title") or vid,
        channel=e.get("channel") or e.get("uploader"),
        duration_ms=round(float(dur) * 1000) if dur else None,
        is_live=live or not dur,
    )


def search(query: str, limit: int = 15) -> list[SearchResult]:
    """`ytsearchN:` com `extract_flat` (sem baixar, ~1–3 s)."""
    import yt_dlp

    opts = _base_opts() | {"extract_flat": "in_playlist", "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    except Exception as exc:  # noqa: BLE001 — yt-dlp levanta vários tipos
        raise classify_error(exc) from exc
    out = []
    for e in (info or {}).get("entries") or []:
        r = _entry_to_result(e or {})
        if r:
            out.append(r)
    return out[:limit]


def fetch_info(youtube_id: str) -> SearchResult:
    """Metadados de um vídeo (sem baixar)."""
    import yt_dlp

    if not YOUTUBE_ID.match(youtube_id):
        raise YoutubeError("video_unavailable", "ID de vídeo inválido.")
    opts = _base_opts() | {"skip_download": True, "extract_flat": False}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={youtube_id}", download=False, process=False
            )
    except Exception as exc:  # noqa: BLE001
        raise classify_error(exc) from exc
    r = _entry_to_result(info or {})
    if r is None:
        raise YoutubeError("video_unavailable", "Este vídeo não está disponível para download.")
    return r


# ---- download ------------------------------------------------------------------------
def download_audio(
    youtube_id: str,
    dest_dir: Path,
    progress: Callable[[float | None], None] | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Baixa o melhor áudio para `dest_dir/source.<ext>`. `progress(0..1|None)`; `cancel` interrompe no próximo hook."""
    import yt_dlp

    if not YOUTUBE_ID.match(youtube_id):
        raise YoutubeError("video_unavailable", "ID de vídeo inválido.")
    dest_dir.mkdir(parents=True, exist_ok=True)

    def hook(d: dict[str, Any]) -> None:
        if cancel is not None and cancel.is_set():
            raise DownloadCanceled()
        if progress is None:
            return
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            progress(min(done / total, 1.0) if total else None)
        elif d.get("status") == "finished":
            progress(1.0)

    opts = _base_opts() | {
        "format": "bestaudio/best",
        "outtmpl": {"default": str(dest_dir / "source.%(ext)s")},
        "progress_hooks": [hook],
        "overwrites": True,
        "continuedl": False,
        "writethumbnail": False,
        "cachedir": False,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={youtube_id}", download=True)
    except DownloadCanceled:
        raise
    except Exception as exc:  # noqa: BLE001
        if cancel is not None and cancel.is_set():
            raise DownloadCanceled() from exc
        if isinstance(exc.__cause__, DownloadCanceled) or "DownloadCanceled" in repr(exc):
            raise DownloadCanceled() from exc
        raise classify_error(exc) from exc
    if info and info.get("is_live"):
        raise YoutubeError("video_unavailable", "Transmissões ao vivo não podem ser processadas.")
    files = sorted(p for p in dest_dir.glob("source.*") if not p.name.endswith((".part", ".ytdl")))
    if not files:
        raise YoutubeError("download_failed", "O download terminou sem gerar arquivo de áudio.")
    return files[0]


# ---- thumbnails -------------------------------------------------------------------------
def _thumb_urls(youtube_id: str, large: bool) -> list[str]:
    names = ["maxresdefault", "hqdefault"] if large else ["mqdefault", "hqdefault"]
    return [f"https://i.ytimg.com/vi/{youtube_id}/{n}.jpg" for n in names]


def fetch_thumbnail(
    youtube_id: str, dest: Path, large: bool = True, transport: httpx.BaseTransport | None = None
) -> bool:
    """Baixa a thumbnail (maxres 1280 px → hq). Escrita atômica. False se indisponível."""
    if not YOUTUBE_ID.match(youtube_id):
        return False
    try:
        with httpx.Client(timeout=THUMB_TIMEOUT_S, transport=transport, follow_redirects=True) as c:
            for url in _thumb_urls(youtube_id, large):
                data = _get_image(c, url)
                if data:
                    _write_atomic(dest, data)
                    return True
    except httpx.HTTPError as exc:
        log.info("thumbnail %s indisponível: %s", youtube_id, exc)
    return False


def _get_image(c: httpx.Client, url: str) -> bytes | None:
    """Corpo de uma resposta 200 image/*, limitado a THUMB_MAX_BYTES (None se maior ou inválida)."""
    with c.stream("GET", url) as r:
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
            return None
        buf = bytearray()
        for chunk in r.iter_bytes():
            buf += chunk
            if len(buf) > THUMB_MAX_BYTES:
                log.info("thumbnail %s maior que %d bytes; ignorada", url, THUMB_MAX_BYTES)
                return None
        return bytes(buf) or None


def _write_atomic(dest: Path, data: bytes) -> None:
    """tmp único no mesmo diretório + rename (requisições simultâneas do mesmo id não se misturam)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=dest.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


class ThumbCache:
    """Cache LRU (por mtime) de thumbnails da busca em data/cache/thumbs (≤ 200 MB)."""

    def __init__(
        self,
        cache_dir: Path,
        max_bytes: int = THUMB_CACHE_MAX_BYTES,
        transport: httpx.BaseTransport | None = None,
    ):
        self.dir = cache_dir
        self.max_bytes = max_bytes
        self.transport = transport
        self._lock = threading.Lock()

    def path_for(self, youtube_id: str) -> Path:
        return self.dir / f"{youtube_id}.jpg"

    def get(self, youtube_id: str) -> Path | None:
        if not YOUTUBE_ID.match(youtube_id):
            return None
        p = self.path_for(youtube_id)
        if p.is_file():
            with contextlib.suppress(OSError):
                os.utime(p)
            return p
        if not fetch_thumbnail(youtube_id, p, large=False, transport=self.transport):
            return None
        self.prune()
        return p

    def prune(self) -> None:
        with self._lock:
            try:
                files = [(f.stat().st_mtime, f.stat().st_size, f) for f in self.dir.glob("*.jpg")]
            except OSError:
                return
            total = sum(s for _, s, _ in files)
            for _, size, f in sorted(files):
                if total <= self.max_bytes:
                    break
                try:
                    f.unlink()
                    total -= size
                except OSError:
                    pass
