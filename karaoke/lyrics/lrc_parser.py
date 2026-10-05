"""Parser de LRC simples e Enhanced LRC (tags [ar:] [ti:] [offset:], <mm:ss.xx> por palavra) → linhas/palavras em ms.

O resultado é "cru" (`ParsedLrc`); quem monta o documento do contrato é `lyrics.builder.from_lrc`.

- Várias tags de tempo na mesma linha (`[00:12.00][01:30.00]Refrão`) geram uma entrada por tag.
- Linhas com tempo e texto vazio são **marcadores** (intervalos instrumentais): ficam em `ParsedLrc.markers`.
- `[offset:+500]` (ms) segue a convenção LRC: positivo = letra adiantada → subtrai do tempo.
- Linhas sem tag de tempo são ignoradas; `first_untimed_line` guarda a primeira (para `422 invalid_lrc`).

Dono: backend-engineer (T-006).
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field

_TIME_TAG = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_META_TAG = re.compile(r"^\[([a-zA-Z#]+):(.*)\]\s*$")
_WORD_TAG = re.compile(r"<(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?>")


@dataclass
class ParsedWord:
    text: str
    start_ms: int
    end_ms: int | None = None  # tag de fechamento, quando houver


@dataclass
class ParsedLine:
    start_ms: int
    text: str
    words: list[ParsedWord] | None = None


@dataclass
class ParsedLrc:
    lines: list[ParsedLine] = field(default_factory=list)
    markers: list[int] = field(default_factory=list)  # timestamps de linhas vazias
    tags: dict[str, str] = field(default_factory=dict)
    offset_ms: int = 0
    first_untimed_line: int | None = None  # nº (1-based) da 1ª linha de texto sem tempo


def _to_ms(m: str, s: str, frac: str | None) -> int:
    ms = int((frac or "0").ljust(3, "0")[:3])  # .5 -> 500, .34 -> 340, .345 -> 345
    return (int(m) * 60 + int(s)) * 1000 + ms


def _parse_words(body: str, line_start: int) -> tuple[str, list[ParsedWord] | None]:
    """Extrai palavras de Enhanced LRC. Retorna (texto limpo, palavras|None)."""
    if not _WORD_TAG.search(body):
        return body.strip(), None
    pieces: list[tuple[int, str]] = []  # (tempo, texto até a próxima tag)
    pos = 0
    current_t: int | None = None
    lead = ""
    for m in _WORD_TAG.finditer(body):
        chunk = body[pos : m.start()]
        if current_t is None:
            lead += chunk
        else:
            pieces.append((current_t, chunk))
        current_t = _to_ms(*m.groups())
        pos = m.end()
    if current_t is not None:
        pieces.append((current_t, body[pos:]))
    words: list[ParsedWord] = []
    if lead.strip():
        for tok in lead.split():
            words.append(ParsedWord(tok, line_start))
    for t, chunk in pieces:
        toks = chunk.split()
        if not toks:
            # tag sem texto depois = fim da palavra anterior
            if words and words[-1].end_ms is None:
                words[-1].end_ms = t
            continue
        # se o pedaço tem várias palavras sem tag, todas começam no mesmo t (o builder distribui)
        for tok in toks:
            words.append(ParsedWord(tok, t))
    text = " ".join(w.text for w in words)
    return text, (words or None)


def parse_lrc(text: str) -> ParsedLrc:
    out = ParsedLrc()
    for n, raw in enumerate((text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"), start=1):
        line = raw.strip().lstrip("﻿")
        if not line:
            continue
        times = []
        pos = 0
        while True:
            m = _TIME_TAG.match(line, pos)
            if not m:
                break
            times.append(_to_ms(*m.groups()))
            pos = m.end()
            while pos < len(line) and line[pos] == " ":
                pos += 1
        if not times:
            meta = _META_TAG.match(line)
            if meta:
                key, val = meta.group(1).lower(), meta.group(2).strip()
                out.tags[key] = val
                if key == "offset":
                    with contextlib.suppress(ValueError):
                        out.offset_ms = int(val.replace("+", ""))
            elif out.first_untimed_line is None:
                out.first_untimed_line = n
            continue
        body = line[pos:]
        for t in times:
            content, words = _parse_words(body, t)
            if not content:
                out.markers.append(t)
            else:
                out.lines.append(
                    ParsedLine(
                        t,
                        content,
                        [ParsedWord(w.text, w.start_ms, w.end_ms) for w in words] if words else None,
                    )
                )
    if out.offset_ms:
        shift = out.offset_ms
        for ln in out.lines:
            ln.start_ms = max(0, ln.start_ms - shift)
            if ln.words:
                for w in ln.words:
                    w.start_ms = max(0, w.start_ms - shift)
                    if w.end_ms is not None:
                        w.end_ms = max(0, w.end_ms - shift)
        out.markers = [max(0, t - shift) for t in out.markers]
    out.lines.sort(key=lambda ln: ln.start_ms)
    out.markers.sort()
    return out
