"""Limpeza de título do YouTube (remove "(Official Video)" etc.) e extração artista/faixa para LRCLIB.

`clean_title("Queen – Bohemian Rhapsody (Official Video Remastered)", "Queen Official")`
→ `CleanTitle(artist="Queen", track="Bohemian Rhapsody")`.

Dono: backend-engineer (T-006).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# palavras que, dentro de () [] 【】, indicam "lixo" de título de vídeo
_NOISE_WORDS = (
    r"official|oficial|video|vídeo|videoclipe|clipe|clip|audio|áudio|lyrics?|letra|legendad[oa]|"
    r"remaster(?:ed)?|remasterizad[oa]|hd|hq|4k|8k|1080p|720p|mv|m/v|visuali[sz]er|"
    r"karaoke|instrumental|explicit|clean|color coded|full album|music|ao vivo|live|"
    r"version|versão|edit|radio|extended|mix|prod\.?|ft\.?|feat\.?|with|stereo|mono|\d{4}"
)
_BRACKETS = re.compile(r"[\(\[【「（]([^\)\]】」）]*)[\)\]】」）]")
_NOISE_IN_BRACKET = re.compile(rf"\b(?:{_NOISE_WORDS})\b", re.IGNORECASE)
_TRAILING_NOISE = re.compile(
    r"\s*(?:[-|/•·:]\s*)?(?:official\s+(?:music\s+)?(?:video|audio|lyric video|visualizer)|"
    r"lyric video|lyrics video|letra|lyrics|audio oficial|video oficial|clipe oficial|"
    r"\bhd\b|\b4k\b)\s*$",
    re.IGNORECASE,
)
_FEAT = re.compile(r"\s+(?:ft\.?|feat\.?|featuring|part\.?|participação)\s+.*$", re.IGNORECASE)
_SEPARATORS = re.compile(r"\s+[-–—|~]\s+|\s*[–—]\s*|\s+-\s*|\s*-\s+")
_CHANNEL_NOISE = re.compile(
    r"\s*(?:-\s*topic|vevo|official|oficial|tv|music|records|channel|canal)\s*$", re.IGNORECASE
)
_QUOTES = re.compile(r"^[\"'“”‘’«»]+|[\"'“”‘’«»]+$")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class CleanTitle:
    artist: str | None
    track: str


def _tidy(s: str) -> str:
    s = _SPACES.sub(" ", s).strip(" -–—|·•:~,")
    return _QUOTES.sub("", s).strip()


def strip_noise(title: str) -> str:
    """Remove trechos entre parênteses/colchetes com palavras de ruído e sufixos tipo "Official Video"."""

    def repl(m: re.Match[str]) -> str:
        return " " if _NOISE_IN_BRACKET.search(m.group(1)) or not m.group(1).strip() else m.group(0)

    s = _BRACKETS.sub(repl, title)
    prev = None
    while prev != s:
        prev = s
        s = _TRAILING_NOISE.sub("", s)
    return _tidy(s)


def clean_channel(channel: str | None) -> str | None:
    if not channel:
        return None
    c = channel.strip()
    prev = None
    while prev != c:
        prev = c
        c = _CHANNEL_NOISE.sub("", c).strip()
    c = re.sub(r"(?<=[a-z])VEVO$", "", c)  # "QueenVEVO" -> "Queen"
    return c or None


_OPEN = "([【「（"
_CLOSE = ")]】」）"


def _split_artist_track(s: str) -> list[str]:
    """Divide no 1º separador fora de parênteses/colchetes ("X (Autoral - Voz e Violão)" não é dividido)."""
    depth_at = []
    depth = 0
    for ch in s:
        depth_at.append(depth)
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE and depth:
            depth -= 1
    for m in _SEPARATORS.finditer(s):
        if depth_at[m.start()] == 0 and depth_at[m.end() - 1] == 0:
            return [s[: m.start()], s[m.end() :]]
    return [s]


def clean_title(title: str, channel: str | None = None) -> CleanTitle:
    """Extrai artista e faixa. Se o título não tem separador, usa o canal (sem "- Topic"/"VEVO") como artista."""
    raw = (title or "").strip()
    s = strip_noise(raw) or raw
    parts = _split_artist_track(s)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        artist, track = _tidy(parts[0]), _tidy(parts[1])
    else:
        artist, track = clean_channel(channel), _tidy(s)
    track = _tidy(_FEAT.sub("", track)) or track
    if artist:
        artist = _tidy(_FEAT.sub("", artist)) or artist
    return CleanTitle(artist=artist or None, track=track or raw)


def normalize(s: str | None) -> str:
    """Minúsculas, sem acento e sem pontuação (comparação tolerante)."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return _SPACES.sub(" ", re.sub(r"[^\w\s]", " ", s)).strip()
