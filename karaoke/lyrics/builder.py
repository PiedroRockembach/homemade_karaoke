"""Monta o documento Lyrics (contracts/lyrics-format.md) a partir de LRC ou de segmentos do Whisper.

Garante as invariantes do §2 do contrato: linhas ordenadas e sem sobreposição, `0 ≤ start < end`, texto aparado,
palavras contíguas/ordenadas com `words[0].start == start` e `words[-1].end == end`, `text == " ".join(words)`.

Dono: backend-engineer (T-006).
"""

from __future__ import annotations

import re
from typing import Any

from karaoke.lyrics.lrc_parser import ParsedLrc, ParsedWord, parse_lrc

MAX_LINE_CHARS = 42
PAUSE_SPLIT_MS = 600
WORD_FALLBACK_MS = 1000

# frases que o Whisper inventa em trechos sem voz
_HALLUCINATIONS = re.compile(
    r"(amara\.org|legendas pela comunidade|legendado por|thanks? for watching|obrigad[oa] por assistir|"
    r"subtitles? by|subscribe|inscreva-se|www\.|\.com\b|♪+$|^\W*$)",
    re.IGNORECASE,
)


# ---- utilidades ------------------------------------------------------------------
def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def estimate_end(start_ms: int, text: str, next_ts: int | None, duration_ms: int | None) -> int:
    """Regra 5 do §2: min(próximo timestamp, start + clamp(palavras × 450 + 600, 1500, 8000)), limitado à duração."""
    est = start_ms + _clamp(len(text.split()) * 450 + 600, 1500, 8000)
    if next_ts is not None and next_ts > start_ms:
        est = min(est, next_ts)
    if duration_ms and duration_ms > start_ms:
        est = min(est, duration_ms)
    return max(est, start_ms + 1)


def _fix_words(words: list[dict[str, Any]], start: int, end: int) -> list[dict[str, Any]] | None:
    """Ajusta palavras para caberem em [start, end], ordenadas, sem sobreposição e com duração > 0."""
    words = [w for w in words if w["text"].strip()]
    if not words or end <= start:
        return None
    n = len(words)
    if end - start < n:  # não cabe 1 ms por palavra: sem palavras
        return None
    out: list[dict[str, Any]] = []
    for i, w in enumerate(words):
        remaining = n - i - 1
        s = _clamp(int(w["start_ms"]), start, end - remaining - 1)
        if out:
            s = max(s, out[-1]["end_ms"])
        e = int(w["end_ms"]) if w.get("end_ms") is not None else s + 1
        e = _clamp(e, s + 1, end - remaining)
        out.append({"text": w["text"].strip(), "start_ms": s, "end_ms": e})
    out[0]["start_ms"] = start
    out[-1]["end_ms"] = end
    return out


def _finalize(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ordena, une linhas com o mesmo início, corta sobreposições e numera `i`."""
    lines = sorted((ln for ln in lines if ln["text"].strip()), key=lambda ln: ln["start_ms"])
    merged: list[dict[str, Any]] = []
    for ln in lines:
        if merged and ln["start_ms"] <= merged[-1]["start_ms"]:
            prev = merged[-1]
            prev["text"] = f"{prev['text']} {ln['text']}"
            prev["end_ms"] = max(prev["end_ms"], ln["end_ms"])
            prev["words"] = (
                (prev["words"] or []) + (ln["words"] or []) if prev["words"] and ln["words"] else None
            )
            if ln.get("interpolated"):
                prev["interpolated"] = True
            continue
        merged.append(dict(ln))
    out: list[dict[str, Any]] = []
    for i, ln in enumerate(merged):
        start = max(0, int(ln["start_ms"]))
        end = int(ln["end_ms"])
        if i + 1 < len(merged):
            end = min(end, int(merged[i + 1]["start_ms"]))
        end = max(end, start + 1)
        words = _fix_words(ln["words"], start, end) if ln.get("words") else None
        text = " ".join(w["text"] for w in words) if words else " ".join(ln["text"].split())
        item: dict[str, Any] = {"i": len(out), "start_ms": start, "end_ms": end, "text": text, "words": words}
        if ln.get("interpolated"):  # tempo estimado (origem `aligned`, lyrics-format §3.1)
            item["interpolated"] = True
        out.append(item)
    return out


def timing_of(lines: list[dict[str, Any]]) -> str:
    if not lines:
        return "none"
    return "word" if all(ln["words"] for ln in lines) else "line"


# ---- LRC -------------------------------------------------------------------------
def _lrc_words(pw: list[ParsedWord], line_start: int, line_end: int) -> list[dict[str, Any]]:
    """Distribui palavras que compartilham o mesmo tempo e estima o fim de cada uma."""
    starts = [max(w.start_ms, line_start) for w in pw]
    i = 0
    while i < len(pw):
        j = i
        while j + 1 < len(pw) and starts[j + 1] == starts[i] and pw[j + 1].start_ms == pw[i].start_ms:
            j += 1
        if j > i:
            nxt = starts[j + 1] if j + 1 < len(pw) else line_end
            span = max(nxt - starts[i], 0)
            for k in range(i, j + 1):
                starts[k] = starts[i] + span * (k - i) // (j - i + 1)
        i = j + 1
    out = []
    for k, w in enumerate(pw):
        nxt = starts[k + 1] if k + 1 < len(pw) else line_end
        end = w.end_ms if w.end_ms is not None and w.end_ms > starts[k] else nxt
        out.append(
            {"text": w.text, "start_ms": starts[k], "end_ms": min(end, nxt) if k + 1 < len(pw) else end}
        )
    return out


def lines_from_parsed(parsed: ParsedLrc, duration_ms: int | None = None) -> list[dict[str, Any]]:
    timestamps = sorted({ln.start_ms for ln in parsed.lines} | set(parsed.markers))
    lines: list[dict[str, Any]] = []
    for ln in parsed.lines:
        nxt = next((t for t in timestamps if t > ln.start_ms), None)
        start = ln.start_ms
        if ln.words:
            start = min(start, ln.words[0].start_ms)
            last = ln.words[-1]
            if last.end_ms is not None and last.end_ms > last.start_ms:
                end = last.end_ms
            else:
                end = last.start_ms + WORD_FALLBACK_MS
                if nxt is not None:
                    end = min(end, nxt)
            if duration_ms and duration_ms > start:
                end = min(end, duration_ms)
            end = max(end, start + 1)
            words = _lrc_words(ln.words, start, end)
            lines.append({"start_ms": start, "end_ms": end, "text": ln.text, "words": words})
        else:
            end = estimate_end(start, ln.text, nxt, duration_ms)
            lines.append({"start_ms": start, "end_ms": end, "text": ln.text, "words": None})
    return _finalize(lines)


def from_lrc(text: str, duration_ms: int | None = None) -> tuple[list[dict[str, Any]], ParsedLrc]:
    parsed = parse_lrc(text)
    return lines_from_parsed(parsed, duration_ms), parsed


# ---- Whisper ----------------------------------------------------------------------
def _is_hallucination(seg: dict[str, Any], prev_texts: list[str]) -> bool:
    text = (seg.get("text") or "").strip()
    if not text or _HALLUCINATIONS.search(text):
        return True
    if (seg.get("no_speech_prob") or 0) > 0.6 and (seg.get("avg_logprob") or 0) < -1.0:
        return True
    if (seg.get("compression_ratio") or 0) > 2.4:
        return True
    norm = re.sub(r"\W+", " ", text.lower()).strip()
    return len(prev_texts) >= 2 and prev_texts[-1] == norm and prev_texts[-2] == norm


def from_whisper(segments: list[dict[str, Any]], duration_ms: int | None = None) -> list[dict[str, Any]]:
    """Segmentos do faster-whisper (tempos em segundos, `words` com word/start/end) → linhas com palavras."""
    lines: list[dict[str, Any]] = []
    prev_texts: list[str] = []
    for seg in segments:
        if _is_hallucination(seg, prev_texts):
            continue
        prev_texts.append(re.sub(r"\W+", " ", (seg.get("text") or "").lower()).strip())
        words = [
            {
                "text": (w.get("word") or "").strip(),
                "start_ms": round(float(w["start"]) * 1000),
                "end_ms": round(float(w["end"]) * 1000),
            }
            for w in seg.get("words") or []
            if (w.get("word") or "").strip()
        ]
        if not words:
            continue
        cur: list[dict[str, Any]] = []
        for w in words:
            if cur:
                chars = len(" ".join(x["text"] for x in cur)) + 1 + len(w["text"])
                pause = w["start_ms"] - cur[-1]["end_ms"]
                punct_break = cur[-1]["text"][-1:] in ",.;!?" and len(" ".join(x["text"] for x in cur)) >= 20
                if pause > PAUSE_SPLIT_MS or chars > MAX_LINE_CHARS or punct_break:
                    lines.append(_line_from_words(cur))
                    cur = []
            cur.append(w)
        if cur:
            lines.append(_line_from_words(cur))
    if duration_ms:
        lines = [ln for ln in lines if ln["start_ms"] < duration_ms]
        for ln in lines:
            ln["end_ms"] = min(ln["end_ms"], duration_ms)
    return _finalize(lines)


def _line_from_words(words: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "start_ms": words[0]["start_ms"],
        "end_ms": max(words[-1]["end_ms"], words[0]["start_ms"] + 1),
        "text": " ".join(w["text"] for w in words),
        "words": [dict(w) for w in words],
    }


# ---- documento ----------------------------------------------------------------------
def make_document(
    *,
    song_id: str,
    candidate_id: str | None,
    source: str | None,
    lines: list[dict[str, Any]],
    language: str | None = None,
    duration_ms: int | None = None,
    offset_ms: int = 0,
    meta: dict[str, Any] | None = None,
    text_lines: list[str] | None = None,
) -> dict[str, Any]:
    meta = meta or {}
    extra = {"text_lines": text_lines} if text_lines else {}  # só texto (timing none): fonte para alinhar
    return {
        **extra,
        "version": 1,
        "song_id": song_id,
        "candidate_id": candidate_id,
        "source": source,
        "timing": timing_of(lines),
        "language": language,
        "duration_ms": duration_ms,
        "offset_ms": offset_ms,
        "lines": lines,
        "meta": {
            "track_name": meta.get("track_name"),
            "artist_name": meta.get("artist_name"),
            "album_name": meta.get("album_name"),
            "provider_id": meta.get("provider_id"),
            "model": meta.get("model"),
            "created_at": meta.get("created_at"),
        },
    }


def empty_document(song_id: str, duration_ms: int | None = None, offset_ms: int = 0) -> dict[str, Any]:
    return make_document(
        song_id=song_id,
        candidate_id=None,
        source=None,
        lines=[],
        duration_ms=duration_ms,
        offset_ms=offset_ms,
    )


def text_lines_of(text: str) -> list[str]:
    """Letra em texto puro (sem tempos) → linhas não vazias, sem tags `[ar:]`/`[ti:]` nem tempos soltos."""
    out = []
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip().lstrip("\ufeff")
        if not line or re.match(r"^\[[a-zA-Z#]+:.*\]$", line):
            continue
        line = " ".join(re.sub(r"[\[<]\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?[\]>]", " ", line).split())
        if line:
            out.append(line[:500])
    return out
