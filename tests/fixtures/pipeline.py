"""Helpers de teste do pipeline (T-006): fakes de LRCLIB/YouTube/worker, músicas prontas no banco, validador
das invariantes de lyrics-format.md §2. Sem rede e sem GPU."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from karaoke import lrclib
from karaoke.db import Database, utcnow
from karaoke.jobs.runner import insert_candidates
from karaoke.lyrics import builder

FIXTURES = Path(__file__).parent
SAMPLE_LRC = (FIXTURES / "sample.lrc").read_text()
ENHANCED_LRC = (FIXTURES / "enhanced.lrc").read_text()

SONG_ID = "fJ9rUzIMcZQ"
OTHER_ID = "dQw4w9WgXcQ"


def lrclib_record(
    id_: int, duration_s: float, lrc: str = SAMPLE_LRC, track: str = "Bohemian Rhapsody"
) -> dict:
    return {
        "id": id_,
        "trackName": track,
        "artistName": "Queen",
        "albumName": "A Night at the Opera",
        "duration": duration_s,
        "instrumental": False,
        "plainLyrics": "x",
        "syncedLyrics": lrc,
    }


def lrclib_transport(
    get: dict | None = None, search: list | None = None, fail: bool = False
) -> httpx.MockTransport:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if fail:
            raise httpx.ConnectError("sem rede", request=request)
        if request.url.path == "/api/get":
            return httpx.Response(200, json=get) if get else httpx.Response(404, json={"code": 404})
        if request.url.path == "/api/search":
            return httpx.Response(200, json=search or [])
        return httpx.Response(404)

    t = httpx.MockTransport(handler)
    t.calls = calls  # type: ignore[attr-defined]
    return t


def lrclib_factory(transport: httpx.MockTransport):  # noqa: ANN201
    return lambda: lrclib.LrclibClient(transport=transport)


def insert_ready_song(
    db: Database,
    song_id: str = SONG_ID,
    added_by: str = "host",
    lrc: str | None = SAMPLE_LRC,
    duration_ms: int = 359000,
) -> str | None:
    now = utcnow()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO songs(id, title, artist, original_title, channel, duration_ms, status, media_version, "
            "size_bytes, added_by, created_at, updated_at) VALUES (?, 'Bohemian Rhapsody', 'Queen', "
            "'Queen – Bohemian Rhapsody (Official Video)', 'Queen Official', ?, 'ready', 1, 100, ?, ?, ?)",
            (song_id, duration_ms, added_by, now, now),
        )
        cid = None
        if lrc:
            lines, _ = builder.from_lrc(lrc, duration_ms)
            cand = {
                "source": "lrclib",
                "timing": builder.timing_of(lines),
                "lines": lines,
                "language": None,
                "meta": {
                    "track_name": "Bohemian Rhapsody",
                    "artist_name": "Queen",
                    "album_name": None,
                    "provider_id": 1,
                    "duration_ms": duration_ms - 1000,
                    "model": None,
                },
            }
            (cid,) = insert_candidates(conn, song_id, [cand], duration_ms)
            conn.execute("UPDATE songs SET lyrics_candidate_id=? WHERE id=?", (cid, song_id))
    return cid


def whisper_segments() -> list[dict[str, Any]]:
    words = [
        "Is",
        "this",
        "the",
        "real",
        "life?",
        "Is",
        "this",
        "just",
        "fantasy?",
        "Caught",
        "in",
        "a",
        "landslide,",
        "no",
        "escape",
        "from",
        "reality",
    ]
    segs, t = [], 10.0
    seg_words = []
    for i, w in enumerate(words):
        seg_words.append({"word": " " + w, "start": t, "end": t + 0.3, "probability": 0.9})
        t += 0.35 if i != 8 else 1.5  # pausa longa depois de "fantasy?"
    segs.append(
        {
            "start": 10.0,
            "end": t,
            "text": " ".join(words),
            "no_speech_prob": 0.01,
            "avg_logprob": -0.2,
            "compression_ratio": 1.2,
            "words": seg_words,
        }
    )
    segs.append(
        {
            "start": t + 1,
            "end": t + 2,
            "text": " Legendas pela comunidade Amara.org",
            "no_speech_prob": 0.9,
            "avg_logprob": -1.5,
            "compression_ratio": 1.0,
            "words": [{"word": " Legendas", "start": t + 1, "end": t + 2, "probability": 0.2}],
        }
    )
    return segs


def assert_lyrics_invariants(lines: list[dict[str, Any]], duration_ms: int | None = None) -> None:
    """lyrics-format.md §2 (1–4)."""
    for i, ln in enumerate(lines):
        assert ln["i"] == i
        assert 0 <= ln["start_ms"] < ln["end_ms"], ln
        assert ln["text"] == ln["text"].strip() and ln["text"]
        if i + 1 < len(lines):
            assert ln["end_ms"] <= lines[i + 1]["start_ms"], (ln, lines[i + 1])
        if duration_ms:
            assert ln["end_ms"] <= duration_ms
        words = ln["words"]
        if words is not None:
            assert words
            assert words[0]["start_ms"] == ln["start_ms"]
            assert words[-1]["end_ms"] == ln["end_ms"]
            assert " ".join(w["text"] for w in words) == ln["text"]
            for a, b in zip(words, words[1:], strict=False):
                assert a["start_ms"] < a["end_ms"] <= b["start_ms"] < b["end_ms"]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())
