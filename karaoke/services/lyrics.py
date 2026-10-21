"""LyricsService: candidatas, seleção, LRC manual, rebusca LRCLIB, regerar com Whisper; grava em lyrics_candidates.

Contrato: api.md §7 e contracts/lyrics-format.md. O documento completo de cada candidata fica em
`lyrics_candidates.lyrics_json` (sem o offset da música, que é aplicado na leitura a partir de `songs.offset_ms`).

Dono: backend-engineer (T-006).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from karaoke import lrclib
from karaoke.db import utcnow
from karaoke.errors import ApiError
from karaoke.jobs.runner import (
    detect_offset_for,
    insert_candidates,
    load_transcript,
    lrclib_candidate,
    song_payload,
)
from karaoke.lyrics import align, builder
from karaoke.lyrics.titleclean import clean_channel

log = logging.getLogger("karaoke")


def song_not_found(song_id: str) -> ApiError:
    return ApiError(404, "song_not_found", "Música não encontrada.", {"song_id": song_id})


def candidate_not_found(candidate_id: str) -> ApiError:
    return ApiError(
        404, "candidate_not_found", "Letra candidata não encontrada.", {"candidate_id": candidate_id}
    )


def song_not_ready(song_id: str) -> ApiError:
    return ApiError(409, "song_not_ready", "A música ainda não está pronta.", {"song_id": song_id})


def network_unavailable() -> ApiError:
    return ApiError(
        502, "network_unavailable", "Não foi possível falar com o LRCLIB. A busca de letras exige internet."
    )


def lrclib_unavailable() -> ApiError:
    """LRCLIB fora do ar/instável (429/5xx/timeout após novas tentativas). Mesmo código do contrato (§7: 502
    `network_unavailable`), mas a mensagem não culpa a internet do usuário."""
    return ApiError(
        502,
        "network_unavailable",
        "O LRCLIB não respondeu agora. Tente de novo em instantes.",
        {"reason": "lrclib_unavailable"},
    )


def all_sources_unavailable() -> ApiError:
    """ADR-0005: busca manual com fontes extras ligadas e todas falharam por rede (AC-23.3)."""
    return ApiError(
        502,
        "network_unavailable",
        "Não foi possível falar com LRCLIB, Vagalume e lyrics.ovh. A busca de letras exige internet.",
        {"reason": "all_sources_unavailable"},
    )


SOURCE_LABELS = {
    "aligned": "Online · sincronizada pela voz",
    "whisper": "Extraída da voz (sem letra oficial)",
    "lrclib": "Online",
    "online_plain": "Online · só texto",
    "manual": "Manual",
}
PLAIN_MANUAL_LABEL = "Colada · só texto"
# ADR-0005 §2: rótulo da `online_plain` por provedor (ausente no meta = lrclib)
PROVIDER_PLAIN_LABELS = {
    "lrclib": "Online · só texto",
    "vagalume": "Vagalume · só texto",
    "lyricsovh": "lyrics.ovh · só texto",
}
# candidatas cujo texto pode ser sincronizado pela voz (texto oficial; CR-001)
TEXT_SOURCES = ("lrclib", "online_plain", "manual", "aligned")
MAX_TIMING_LINES = 2000
MAX_LINE_CHARS = 500
MAX_LINE_WORDS = 200


def candidate_summary(row: sqlite3.Row) -> dict[str, Any]:
    meta = json.loads(row["meta_json"] or "{}")
    text_only = row["timing"] == "none"
    label = SOURCE_LABELS.get(row["source"], row["source"])
    if row["source"] == "manual" and text_only:
        label = PLAIN_MANUAL_LABEL
    provider = (
        (meta.get("provider") or "lrclib") if row["source"] in ("lrclib", "online_plain", "aligned") else None
    )
    if row["source"] == "online_plain":
        label = PROVIDER_PLAIN_LABELS.get(provider, label)
    out: dict[str, Any] = {
        "id": row["id"],
        "source": row["source"],
        "provider": provider,
        "label": label,
        "timing": row["timing"],
        "official_text": row["source"]
        != "whisper",  # CR-001: false = texto do Whisper (sugerir colar a letra)
        "selectable": not text_only,  # só texto (sem tempos) serve de base para alinhar, não para tocar
        "derived_from": row["derived_from"] or meta.get("derived_from"),
        "duration_ms": meta.get("duration_ms"),
        "duration_diff_ms": meta.get("duration_diff_ms"),
        "line_count": meta.get("line_count", 0),
        "preview": meta.get("preview", []),
        "created_at": row["created_at"],
    }
    if row["source"] in ("lrclib", "online_plain"):
        out.update(
            track_name=meta.get("track_name"),
            artist_name=meta.get("artist_name"),
            album_name=meta.get("album_name"),
        )
    elif row["source"] == "whisper":
        out.update(model=meta.get("model"), language=meta.get("language"))
    elif row["source"] == "aligned":
        a = meta.get("align") or {}
        out.update(
            track_name=meta.get("track_name"),
            artist_name=meta.get("artist_name"),
            album_name=meta.get("album_name"),
            model=meta.get("model"),
            language=meta.get("language"),
            align={k: a.get(k) for k in ("coverage", "anchored_lines", "lines", "offset_ms")},
        )
    else:
        out.update(
            track_name=meta.get("track_name"),
            artist_name=meta.get("artist_name"),
            edited=bool(meta.get("editor")),
        )
    return out


class TimingWord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = Field(min_length=1, max_length=MAX_LINE_CHARS)
    start_ms: int = Field(ge=0, le=24 * 3600 * 1000)
    end_ms: int = Field(ge=0, le=24 * 3600 * 1000)


class TimingLine(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = Field(min_length=1, max_length=MAX_LINE_CHARS)
    start_ms: int = Field(ge=0, le=24 * 3600 * 1000)
    end_ms: int | None = Field(default=None, ge=1, le=24 * 3600 * 1000)
    words: list[TimingWord] | None = Field(default=None, max_length=MAX_LINE_WORDS)


def _scale_words(
    words: list[dict[str, Any]], old: tuple[int, int], new: tuple[int, int]
) -> list[dict[str, Any]]:
    """Leva as palavras de [old] para [new] preservando as proporções (linha deslocada/esticada no editor)."""
    o0, o1 = old
    n0, n1 = new
    span_o = max(o1 - o0, 1)

    def f(t: int) -> int:
        return round(n0 + (t - o0) * (n1 - n0) / span_o)

    return [{"text": w["text"], "start_ms": f(w["start_ms"]), "end_ms": f(w["end_ms"])} for w in words]


def build_manual_lines(
    lines: list[TimingLine], base_lines: list[dict[str, Any]], duration_ms: int | None
) -> list[dict[str, Any]]:
    """Linhas editadas → linhas do contrato. Valida (ordem, duração, palavras) e completa o que faltar:
    `end_ms` ausente = duração original da linha (mesmo texto e posição na base) ou a estimativa do §2.5;
    `words` ausentes = palavras da base reescaladas para o novo intervalo, ou divisão proporcional por caracteres.
    Levanta ApiError 422 `invalid_timing` com `details.line` (índice 0-based) e `details.reason`."""

    def bad(i: int, reason: str, message: str) -> ApiError:
        return ApiError(422, "invalid_timing", message, {"line": i, "reason": reason})

    out: list[dict[str, Any]] = []
    for i, ln in enumerate(lines):
        text = " ".join(ln.text.split())
        if not text:
            raise bad(i, "empty_text", f"A linha {i + 1} está vazia.")
        if i and ln.start_ms <= lines[i - 1].start_ms:
            raise bad(i, "not_sorted", f"A linha {i + 1} começa antes (ou junto) da anterior.")
        if duration_ms and ln.start_ms >= duration_ms:
            raise bad(i, "after_end", f"A linha {i + 1} começa depois do fim da música.")
        if ln.end_ms is not None and ln.end_ms <= ln.start_ms:
            raise bad(i, "end_before_start", f"A linha {i + 1} termina antes de começar.")
        nxt = lines[i + 1].start_ms if i + 1 < len(lines) else None
        base = base_lines[i] if i < len(base_lines) else None
        same = base is not None and " ".join(base["text"].split()) == text
        end = ln.end_ms
        if end is None:
            if same:
                end = ln.start_ms + max(int(base["end_ms"]) - int(base["start_ms"]), 1)
                if nxt is not None:
                    end = min(end, nxt)
            else:
                end = builder.estimate_end(ln.start_ms, text, nxt, duration_ms)
        if duration_ms:
            end = min(end, duration_ms)
        end = max(end, ln.start_ms + 1)
        if ln.words:
            words = [{"text": w.text.strip(), "start_ms": w.start_ms, "end_ms": w.end_ms} for w in ln.words]
            if " ".join(w["text"] for w in words) != text:
                raise bad(i, "words_mismatch", f"As palavras da linha {i + 1} não batem com o texto.")
            if any(b["start_ms"] < a["start_ms"] for a, b in zip(words, words[1:], strict=False)):
                raise bad(i, "words_not_sorted", f"As palavras da linha {i + 1} estão fora de ordem.")
        elif same and base.get("words"):
            words = _scale_words(
                base["words"], (int(base["start_ms"]), int(base["end_ms"])), (ln.start_ms, end)
            )
        else:
            toks = text.split()
            words = [
                {"text": t, "start_ms": s, "end_ms": e}
                for t, (s, e) in zip(toks, align._distribute(toks, ln.start_ms, end), strict=True)
            ]
        out.append({"start_ms": ln.start_ms, "end_ms": end, "text": text, "words": words})
    return builder._finalize(out)


class LyricsService:
    def __init__(self, app: FastAPI, lrclib_factory: Callable[[], lrclib.LrclibClient] | None = None):
        self.app = app
        self.lrclib_factory = lrclib_factory

    @property
    def db(self):  # noqa: ANN201
        return self.app.state.db

    @property
    def bus(self):  # noqa: ANN201
        return self.app.state.bus

    def _client(self) -> lrclib.LrclibClient:
        factory = (
            self.lrclib_factory or getattr(self.app.state, "lrclib_factory", None) or lrclib.LrclibClient
        )
        return factory()

    def _song_row(self, conn: sqlite3.Connection, song_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()
        if row is None:
            raise song_not_found(song_id)
        return row

    # ---- leitura ----------------------------------------------------------------------------
    def document(self, song_id: str) -> dict[str, Any]:
        conn = self.db.connect()
        try:
            song = self._song_row(conn, song_id)
            return self._document(conn, song)
        finally:
            conn.close()

    def _document(self, conn: sqlite3.Connection, song: sqlite3.Row) -> dict[str, Any]:
        cid = song["lyrics_candidate_id"]
        row = (
            conn.execute("SELECT lyrics_json FROM lyrics_candidates WHERE id = ?", (cid,)).fetchone()
            if cid
            else None
        )
        if row is None:
            return builder.empty_document(song["id"], song["duration_ms"], int(song["offset_ms"] or 0))
        doc = json.loads(row["lyrics_json"])
        doc["offset_ms"] = int(song["offset_ms"] or 0)
        doc["duration_ms"] = song["duration_ms"] or doc.get("duration_ms")
        return doc

    def candidates(self, song_id: str) -> dict[str, Any]:
        conn = self.db.connect()
        try:
            song = self._song_row(conn, song_id)
            return self._candidates(conn, song)
        finally:
            conn.close()

    def _candidates(self, conn: sqlite3.Connection, song: sqlite3.Row) -> dict[str, Any]:
        rows = conn.execute("SELECT * FROM lyrics_candidates WHERE song_id = ?", (song["id"],)).fetchall()
        selected = song["lyrics_candidate_id"]
        items = [candidate_summary(r) for r in rows]

        def key(c: dict[str, Any]) -> tuple:
            diff = c.get("duration_diff_ms")
            return (
                0 if c["id"] == selected else 1,
                abs(diff) if diff is not None else 10**9,
                c["created_at"] or "",
            )

        items.sort(key=key)
        return {
            "selected_id": selected if any(c["id"] == selected for c in items) else None,
            "candidates": items,
        }

    # ---- mutações ---------------------------------------------------------------------------------
    def _publish_selection(self, song_id: str, candidate_id: str, source: str) -> None:
        self.bus.publish(
            "lyrics.updated", {"song_id": song_id, "candidate_id": candidate_id, "source": source}
        )
        conn = self.db.connect()
        try:
            song = song_payload(conn, song_id, "host")
        finally:
            conn.close()
        if song is not None:
            self.bus.publish("song.updated", song)
            self.bus.publish("library.updated", {"action": "updated", "song_id": song_id, "song": song})

    def select(self, song_id: str, candidate_id: str) -> dict[str, Any]:
        with self.db.tx() as conn:
            self._song_row(conn, song_id)
            cand = conn.execute(
                "SELECT id, source, timing FROM lyrics_candidates WHERE id = ? AND song_id = ?",
                (candidate_id, song_id),
            ).fetchone()
            if cand is None:
                raise candidate_not_found(candidate_id)
            if cand["timing"] == "none":
                raise ApiError(
                    409,
                    "candidate_not_selectable",
                    "Esta letra não tem tempos. Use “Sincronizar pela voz” para gerar uma versão sincronizada.",
                    {"candidate_id": candidate_id},
                )
            conn.execute(
                "UPDATE songs SET lyrics_candidate_id = ?, updated_at = ? WHERE id = ?",
                (candidate_id, utcnow(), song_id),
            )
            doc = self._document(conn, self._song_row(conn, song_id))
        self._publish_selection(song_id, candidate_id, cand["source"])
        return doc

    def manual(self, song_id: str, lrc: str, actor: str = "host") -> tuple[dict[str, Any], bool]:
        """LRC colado → (Lyrics, False). Texto puro, sem nenhuma tag de tempo (CR-001) → candidata `manual` só texto
        + job `align` (se a música estiver pronta) → ({"candidate", "job"}, True)."""
        conn = self.db.connect()
        try:
            song = self._song_row(conn, song_id)
        finally:
            conn.close()
        lines, parsed = builder.from_lrc(lrc, song["duration_ms"])
        if not lines and not parsed.markers:
            texts = builder.text_lines_of(lrc)
            if texts:
                return self._manual_plain(song, texts, parsed.tags, actor), True
        if not lines:
            raise ApiError(
                422,
                "invalid_lrc",
                "A letra colada não tem nenhuma linha com tempo no formato [mm:ss.xx].",
                {"line": parsed.first_untimed_line},
            )
        cand = {
            "source": "manual",
            "timing": builder.timing_of(lines),
            "lines": lines,
            "language": None,
            "meta": {
                "track_name": parsed.tags.get("ti"),
                "artist_name": parsed.tags.get("ar"),
                "album_name": parsed.tags.get("al"),
                "provider_id": None,
                "duration_ms": None,
                "model": None,
            },
        }
        with self.db.tx() as conn:
            self._song_row(conn, song_id)
            (cid,) = insert_candidates(conn, song_id, [cand], song["duration_ms"])
            conn.execute(
                "UPDATE songs SET lyrics_candidate_id = ?, updated_at = ? WHERE id = ?",
                (cid, utcnow(), song_id),
            )
            doc = self._document(conn, self._song_row(conn, song_id))
        self._publish_selection(song_id, cid, "manual")
        return doc, False

    def _manual_plain(
        self, song: sqlite3.Row, texts: list[str], tags: dict[str, str], actor: str
    ) -> dict[str, Any]:
        song_id = song["id"]
        cand = {
            "source": "manual",
            "timing": "none",
            "lines": [],
            "text_lines": texts[:MAX_TIMING_LINES],
            "language": None,
            "meta": {
                "track_name": tags.get("ti"),
                "artist_name": tags.get("ar"),
                "album_name": tags.get("al"),
                "provider_id": None,
                "duration_ms": None,
                "model": None,
            },
        }
        with self.db.tx() as conn:
            self._song_row(conn, song_id)
            (cid,) = insert_candidates(conn, song_id, [cand], song["duration_ms"])
            row = conn.execute("SELECT * FROM lyrics_candidates WHERE id = ?", (cid,)).fetchone()
        job = None
        if song["status"] == "ready":
            job = self._enqueue_lyrics_job(song_id, "align", actor, {"candidate_id": cid}, replace=True)[
                "job"
            ]
        s = None
        conn = self.db.connect()
        try:
            s = song_payload(conn, song_id, "host")
        finally:
            conn.close()
        if s is not None:
            self.bus.publish("song.updated", s)
        return {"candidate": candidate_summary(row), "job": job}

    def search(self, song_id: str, track_name: str | None, artist_name: str | None) -> dict[str, Any]:
        """Busca manual (ADR-0005 `exhaustive=True`): LRCLIB e, se ligadas, todas as fontes extras."""
        from karaoke.api.settings import get_lyrics_provider_settings
        from karaoke.lyrics_providers import build_providers, chain

        conn = self.db.connect()
        try:
            song = self._song_row(conn, song_id)
            known: set[Any] = set()
            known_prints: set[str] = set()
            for r in conn.execute(
                "SELECT source, meta_json FROM lyrics_candidates WHERE song_id = ? "
                "AND source IN ('lrclib','online_plain','manual')",
                (song_id,),
            ):
                meta = json.loads(r["meta_json"] or "{}")
                if r["source"] != "manual":
                    known.add(meta.get("provider_id"))
                known_prints.add(chain.fingerprint(meta.get("preview") or []))
        finally:
            conn.close()
        track = (track_name or "").strip() or song["title"]
        artist = (artist_name or "").strip() or song["artist"] or clean_channel(song["channel"])
        settings = get_lyrics_provider_settings(self.db, self.app.state.config)
        factory = getattr(self.app.state, "lyrics_providers_factory", None) or build_providers
        providers = factory(settings)
        client: lrclib.LrclibClient | None = None
        try:
            client = self._client()
            found = chain.search_lyrics(
                client,
                providers,
                track,
                artist,
                song["duration_ms"],
                song["original_title"],
                exhaustive=True,
                known=known,
                known_prints=known_prints,
            )
        finally:
            if client is not None:
                client.close()
            chain.close_all(providers)
        if found.lrclib_error is not None and not providers:  # fontes extras desligadas: comportamento atual
            exc = found.lrclib_error
            if isinstance(exc, lrclib.LrclibUnavailableError):
                log.warning("LRCLIB indisponível: %s", type(exc).__name__)
                raise lrclib_unavailable() from exc
            log.warning("sem conexão com o LRCLIB: %s", type(exc).__name__)
            raise network_unavailable() from exc
        if providers and found.all_network_failed():
            log.warning("busca de letra: todas as fontes falharam por rede %s", found.status)
            raise all_sources_unavailable()
        new = [
            c for r in found.records if r.id not in known and (c := lrclib_candidate(r, song["duration_ms"]))
        ]
        new += [chain.plain_candidate(p) for p in found.extra]
        with self.db.tx() as conn:
            self._song_row(conn, song_id)
            if new:
                insert_candidates(conn, song_id, new, song["duration_ms"])
            result = self._candidates(conn, self._song_row(conn, song_id))
        if new:
            s = None
            conn = self.db.connect()
            try:
                s = song_payload(conn, song_id, "host")
            finally:
                conn.close()
            if s is not None:
                self.bus.publish("song.updated", s)
        return {"added": len(new), "candidates": result["candidates"], "sources": found.status}

    # ---- v0.2: sincronizar pela voz, editor manual, detectar offset ------------------------------------
    def _text_base(
        self, conn: sqlite3.Connection, song: sqlite3.Row, candidate_id: str | None
    ) -> sqlite3.Row | None:
        """Candidata cujo texto será alinhado: a pedida; senão a selecionada (se tiver texto oficial); senão a online
        (com tempos antes de só texto) de duração mais próxima; senão a colada mais recente. Uma `aligned` é trocada
        pela sua origem (`derived_from`). Nenhuma (sem `candidate_id`) → None: o job busca a letra oficial."""
        rows = conn.execute("SELECT * FROM lyrics_candidates WHERE song_id = ?", (song["id"],)).fetchall()
        by_id = {r["id"]: r for r in rows}
        if candidate_id:
            row = by_id.get(candidate_id)
            if row is None:
                raise candidate_not_found(candidate_id)
        else:
            row = by_id.get(song["lyrics_candidate_id"] or "")
            if row is None or row["source"] not in TEXT_SOURCES:
                online = [r for r in rows if r["source"] in ("lrclib", "online_plain")]
                online.sort(
                    key=lambda r: (
                        r["source"] != "lrclib",
                        abs(json.loads(r["meta_json"] or "{}").get("duration_diff_ms") or 10**9),
                    )
                )
                pasted = sorted((r for r in rows if r["source"] == "manual"), key=lambda r: r["created_at"])
                row = online[0] if online else (pasted[-1] if pasted else None)
        if row is not None and row["source"] == "aligned" and by_id.get(row["derived_from"] or ""):
            row = by_id[row["derived_from"]]
        if row is not None and row["source"] not in TEXT_SOURCES:
            raise ApiError(
                409,
                "lyrics_text_required",
                "Esta letra foi extraída da voz. Escolha uma letra online ou colada para sincronizar.",
                {"song_id": song["id"], "candidate_id": row["id"]},
            )
        return row

    def _enqueue_lyrics_job(
        self, song_id: str, kind: str, actor: str, options: dict[str, Any], replace: bool = False
    ) -> dict[str, Any]:
        """Job de letra; reaproveita o ativo do mesmo tipo (com `replace`, só se ainda não começou e tem as mesmas
        opções — senão enfileira outro)."""
        runner = self.app.state.runner
        with self.db.tx() as conn:
            song = self._song_row(conn, song_id)
            if song["status"] != "ready":
                raise song_not_ready(song_id)
            active = runner.active_job_for(conn, song_id, kind)
            if active is not None and replace:
                same = json.loads(active["options_json"] or "{}").get("candidate_id") == options.get(
                    "candidate_id"
                )
                if not same:
                    active = None
            job_id = active["id"] if active else None
            if job_id is None:
                job_id = runner.insert_job(conn, kind, song_id, actor, options)
        if active is None:
            runner.notify_created(job_id)
        return {"job": runner.job_dict(job_id)}

    def align(
        self, song_id: str, candidate_id: str | None, language: str | None, actor: str
    ) -> dict[str, Any]:
        conn = self.db.connect()
        try:
            song = self._song_row(conn, song_id)
            if song["status"] != "ready":
                raise song_not_ready(song_id)
            base = self._text_base(conn, song, candidate_id)
        finally:
            conn.close()
        if base is None:  # sem letra oficial guardada: o job busca no LRCLIB antes (CR-001)
            return self._enqueue_lyrics_job(
                song_id, "align", actor, {"candidate_id": None, "search": True, "language": language}
            )
        return self._enqueue_lyrics_job(
            song_id, "align", actor, {"candidate_id": base["id"], "language": language}
        )

    def manual_timing(
        self, song_id: str, candidate_id: str | None, lines: list[TimingLine]
    ) -> tuple[dict[str, Any], bool]:
        """Salva os tempos do editor. Editar uma candidata `manual` criada pelo editor atualiza-a no lugar;
        qualquer outra origem gera uma nova `manual:<n>` com `derived_from` (a original continua listada).
        Retorna (documento, criada?)."""
        conn = self.db.connect()
        try:
            song = self._song_row(conn, song_id)
            cid = candidate_id or song["lyrics_candidate_id"]
            base = (
                conn.execute(
                    "SELECT * FROM lyrics_candidates WHERE id = ? AND song_id = ?", (cid, song_id)
                ).fetchone()
                if cid
                else None
            )
        finally:
            conn.close()
        if candidate_id and base is None:
            raise candidate_not_found(candidate_id)
        base_lines = (json.loads(base["lyrics_json"]).get("lines") or []) if base is not None else []
        base_meta = json.loads(base["meta_json"] or "{}") if base is not None else {}
        built = build_manual_lines(lines, base_lines, song["duration_ms"])
        update_in_place = base is not None and base["source"] == "manual" and bool(base_meta.get("editor"))
        derived = (
            (base_meta.get("derived_from") if update_in_place else base["id"]) if base is not None else None
        )
        meta = {
            "track_name": base_meta.get("track_name"),
            "artist_name": base_meta.get("artist_name"),
            "album_name": base_meta.get("album_name"),
            "provider_id": base_meta.get("provider_id"),
            "duration_ms": base_meta.get("duration_ms"),
            "model": None,
            "editor": True,
            "language": base_meta.get("language"),
        }
        with self.db.tx() as conn:
            self._song_row(conn, song_id)
            if update_in_place:
                new_id = base["id"]
                now = utcnow()
                meta.update(
                    derived_from=derived,
                    created_at=base_meta.get("created_at") or base["created_at"],
                    updated_at=now,
                    duration_diff_ms=base_meta.get("duration_diff_ms"),
                    line_count=len(built),
                    preview=[ln["text"] for ln in built[:3]],
                )
                doc = builder.make_document(
                    song_id=song_id,
                    candidate_id=new_id,
                    source="manual",
                    lines=built,
                    language=meta["language"],
                    duration_ms=song["duration_ms"],
                    meta=meta,
                )
                conn.execute(
                    "UPDATE lyrics_candidates SET timing=?, meta_json=?, lyrics_json=? WHERE id=?",
                    (
                        doc["timing"],
                        json.dumps(meta, ensure_ascii=False),
                        json.dumps(doc, ensure_ascii=False),
                        new_id,
                    ),
                )
            else:
                cand = {
                    "source": "manual",
                    "timing": builder.timing_of(built),
                    "lines": built,
                    "language": meta["language"],
                    "meta": meta,
                    "derived_from": derived,
                }
                (new_id,) = insert_candidates(conn, song_id, [cand], song["duration_ms"])
            conn.execute(
                "UPDATE songs SET lyrics_candidate_id = ?, updated_at = ? WHERE id = ?",
                (new_id, utcnow(), song_id),
            )
            doc = self._document(conn, self._song_row(conn, song_id))
        self._publish_selection(song_id, new_id, "manual")
        return doc, not update_in_place

    def detect_offset(
        self, song_id: str, candidate_id: str | None, actor: str
    ) -> tuple[dict[str, Any], bool]:
        """Sugestão de offset global. Com transcrição em cache → (resultado, False) na hora; sem cache →
        ({"job": Job}, True) e o resultado chega pelo evento `lyrics.offset_detected`."""
        conn = self.db.connect()
        try:
            song = self._song_row(conn, song_id)
            cid = candidate_id or song["lyrics_candidate_id"]
            base = (
                conn.execute(
                    "SELECT * FROM lyrics_candidates WHERE id = ? AND song_id = ?", (cid, song_id)
                ).fetchone()
                if cid
                else None
            )
        finally:
            conn.close()
        if base is None:
            if candidate_id:
                raise candidate_not_found(candidate_id)
            raise ApiError(409, "no_lyrics", "Esta música está sem letra selecionada.", {"song_id": song_id})
        if song["status"] != "ready":
            raise song_not_ready(song_id)
        transcript = load_transcript(self.app.state.config, song_id)
        if transcript is None:
            return self._enqueue_lyrics_job(song_id, "analyze", actor, {"candidate_id": base["id"]}), True
        result = detect_offset_for(base, transcript)
        return {
            "song_id": song_id,
            "candidate_id": base["id"],
            "current_offset_ms": int(song["offset_ms"] or 0),
            **result,
        }, False

    def transcribe(self, song_id: str, language: str | None, actor: str) -> dict[str, Any]:
        return self._enqueue_lyrics_job(song_id, "transcribe", actor, {"language": language})


def startup(app: FastAPI) -> None:
    app.state.lyrics = LyricsService(app)
