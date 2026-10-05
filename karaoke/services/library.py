"""LibraryService: músicas (objeto Song do contrato §6, com `added_by` e `can_remove`), busca local e exclusão
(arquivos + linhas) com a regra de posse.

Também define a infraestrutura comum aos serviços de estado (fila/player):
- `song_to_dict(row, actor, conn=None)` — serializa uma música. Usado por fila/player e pelo T-006 (jobs).
- `load_song(conn, song_id, actor)` — busca + serializa (None se não existe).
- `transaction(app)` — transação SQLite sob o lock de estado compartilhado; ao sair (após o COMMIT) publica
  `queue.updated` / `player.state` / eventos extras marcados no contexto. Use em qualquer mutação que mexa em
  fila/player para manter a ordem dos eventos.

Dono: backend-engineer (T-005).
"""

from __future__ import annotations

import copy
import logging
import shutil
import sqlite3
import threading
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI

from karaoke.errors import ApiError
from karaoke.security import can_remove, ensure_can_remove

log = logging.getLogger("karaoke")

ACTIVE_JOB_STAGES = ("queued", "model_download", "downloading", "separating", "lyrics", "finalizing")
_ACTIVE_SQL = ",".join(f"'{s}'" for s in ACTIVE_JOB_STAGES)

# Linha de música com tudo o que o objeto Song precisa (job ativo + resumo da letra selecionada).
SONG_SELECT = """
SELECT s.*,
       (SELECT j.id FROM jobs j WHERE j.song_id = s.id AND j.stage IN ('queued','model_download','downloading','separating','lyrics','finalizing')
         ORDER BY j.created_at DESC, j.rowid DESC LIMIT 1) AS active_job_id,
       lc.source AS lyrics_source,
       lc.timing AS lyrics_timing,
       (SELECT COUNT(*) FROM lyrics_candidates c WHERE c.song_id = s.id) AS lyrics_candidates
  FROM songs s
  LEFT JOIN lyrics_candidates lc ON lc.id = s.lyrics_candidate_id
"""

SONG_STATUSES = ("queued", "processing", "ready", "error")


def song_not_found(song_id: str) -> ApiError:
    return ApiError(404, "song_not_found", "Música não encontrada.", {"song_id": song_id})


def normalize(text: str | None) -> str:
    """Minúsculas sem acentos (busca/ordenação case- e acento-insensível)."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold().strip()


def _get(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def song_to_dict(row: Any, actor: str = "host", conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Objeto Song do contrato §6.

    `row` pode ser uma linha de `SONG_SELECT` (recomendado) ou de `SELECT * FROM songs`; neste caso, passe `conn`
    para completar job ativo e resumo da letra (senão vêm vazios).
    """
    song_id = row["id"]
    keys = row.keys() if hasattr(row, "keys") else ()
    if "active_job_id" not in keys and conn is not None:
        full = conn.execute(SONG_SELECT + " WHERE s.id = ?", (song_id,)).fetchone()
        if full is not None:
            row = full
    status = row["status"]
    ready = status == "ready"
    version = max(int(_get(row, "media_version", 0) or 0), 1)
    candidate_id = _get(row, "lyrics_candidate_id")
    source = _get(row, "lyrics_source") if candidate_id else None
    song = {
        "id": song_id,
        "youtube_id": song_id,
        "title": row["title"],
        "artist": _get(row, "artist"),
        "original_title": _get(row, "original_title"),
        "channel": _get(row, "channel"),
        "duration_ms": _get(row, "duration_ms"),
        "status": status,
        "job_id": _get(row, "active_job_id"),
        "thumbnail": f"/media/songs/{song_id}/thumb.jpg" if ready else f"/api/thumbs/{song_id}",
        "lyrics": {
            "source": source,
            "timing": (_get(row, "lyrics_timing") or "none") if source else "none",
            "candidate_id": candidate_id if source else None,
            "candidates": int(_get(row, "lyrics_candidates", 0) or 0),
        },
        "offset_ms": int(_get(row, "offset_ms", 0) or 0),
        "media": (
            {
                "instrumental": f"/media/songs/{song_id}/instrumental.opus?v={version}",
                "vocals": f"/media/songs/{song_id}/vocals.opus?v={version}",
                "version": version,
            }
            if ready
            else None
        ),
        "size_bytes": int(_get(row, "size_bytes", 0) or 0),
        "added_by": _get(row, "added_by", "host"),
        "can_remove": False,
        "created_at": _get(row, "created_at"),
    }
    song["can_remove"] = can_remove(actor, song)
    return song


def load_song_row(conn: sqlite3.Connection, song_id: str) -> sqlite3.Row | None:
    return conn.execute(SONG_SELECT + " WHERE s.id = ?", (song_id,)).fetchone()


def load_song(conn: sqlite3.Connection, song_id: str, actor: str = "host") -> dict[str, Any] | None:
    row = load_song_row(conn, song_id)
    return song_to_dict(row, actor) if row is not None else None


# ---- transação com publicação de eventos ---------------------------------------------------------------
@dataclass
class Txn:
    conn: sqlite3.Connection
    queue_changed: bool = False
    player_changed: bool = False
    events: list[tuple[str, Any]] = field(default_factory=list)

    def emit(self, type_: str, data: Any) -> None:
        self.events.append((type_, data))


def state_lock(app: FastAPI) -> threading.RLock:
    lock = getattr(app.state, "state_lock", None)
    if lock is None:
        lock = app.state.state_lock = threading.RLock()
    return lock


@contextmanager
def transaction(app: FastAPI) -> Iterator[Txn]:
    """Transação sob o lock de estado; publica eventos só depois do COMMIT (na ordem: fila, player, extras)."""
    with state_lock(app):
        queue_svc = getattr(app.state, "queue", None)
        player_svc = getattr(app.state, "player", None)
        # o estado do player em memória acompanha o ROLLBACK do banco
        snapshot = copy.deepcopy(player_svc.s) if player_svc is not None else None
        try:
            with app.state.db.tx() as conn:
                txn = Txn(conn)
                yield txn
                if txn.queue_changed and queue_svc is not None:
                    queue_svc.bump_revision(conn)
                if txn.player_changed and player_svc is not None:
                    player_svc.save(conn)
        except BaseException:
            if snapshot is not None:
                player_svc.s.clear()
                player_svc.s.update(snapshot)
            raise
        bus = app.state.bus
        if txn.queue_changed and queue_svc is not None:
            queue_svc.publish()
        if txn.player_changed and player_svc is not None:
            player_svc.publish()
        for type_, data in txn.events:
            bus.publish(type_, data)


# ---- serviço ----------------------------------------------------------------------------------------
class LibraryService:
    def __init__(self, app: FastAPI):
        self.app = app

    @property
    def db(self):  # noqa: ANN201
        return self.app.state.db

    def get(self, song_id: str, actor: str) -> dict[str, Any]:
        conn = self.db.connect()
        try:
            song = load_song(conn, song_id, actor)
        finally:
            conn.close()
        if song is None:
            raise song_not_found(song_id)
        return song

    def list(self, q: str = "", status: str = "ready", sort: str = "recent", actor: str = "host") -> dict:
        conn = self.db.connect()
        try:
            sql = SONG_SELECT + (" WHERE s.status = 'ready'" if status == "ready" else "")
            rows = conn.execute(sql).fetchall()
        finally:
            conn.close()
        needle = normalize(q)
        if needle:
            rows = [r for r in rows if needle in normalize(r["title"]) or needle in normalize(r["artist"])]
        if sort == "title":
            rows.sort(key=lambda r: (normalize(r["title"]), normalize(r["artist"])))
        elif sort == "artist":
            rows.sort(key=lambda r: (normalize(r["artist"]) or "￿", normalize(r["title"])))
        else:
            rows.sort(key=lambda r: (r["created_at"] or "", r["id"]), reverse=True)
        songs = [song_to_dict(r, actor) for r in rows]
        return {"songs": songs, "total": len(songs)}

    def update(self, song_id: str, patch: dict[str, Any], actor: str) -> dict[str, Any]:
        """PATCH title/artist/offset_ms. Eventos song.updated + library.updated (+ fila/player se afetados)."""
        from karaoke.db import utcnow

        with transaction(self.app) as txn:
            conn = txn.conn
            if load_song_row(conn, song_id) is None:
                raise song_not_found(song_id)
            if patch:
                cols = ", ".join(f"{k} = ?" for k in patch)
                conn.execute(
                    f"UPDATE songs SET {cols}, updated_at = ? WHERE id = ?",  # noqa: S608 (chaves validadas)
                    (*patch.values(), utcnow(), song_id),
                )
                in_queue = conn.execute(
                    "SELECT 1 FROM queue_items WHERE song_id = ? LIMIT 1", (song_id,)
                ).fetchone()
                txn.queue_changed = in_queue is not None
                player = getattr(self.app.state, "player", None)
                if player is not None and player.involves_song(song_id):
                    txn.player_changed = True
            song = load_song(conn, song_id, actor)
            if patch:
                txn.emit("song.updated", song)
                txn.emit("library.updated", {"action": "updated", "song_id": song_id, "song": song})
        return song

    def delete(self, song_id: str, actor: str) -> None:
        """DELETE: regra de posse, 409 se tocando, cancela job ativo, apaga linhas (cascata) e arquivos."""
        with transaction(self.app) as txn:
            conn = txn.conn
            row = conn.execute("SELECT id, added_by FROM songs WHERE id = ?", (song_id,)).fetchone()
            if row is None:
                raise song_not_found(song_id)
            ensure_can_remove(actor, row, song_id)
            player = getattr(self.app.state, "player", None)
            if player is not None and player.song_in_use(song_id):
                raise ApiError(
                    409,
                    "song_in_use",
                    "A música está tocando. Pule-a antes de excluir.",
                    {"song_id": song_id},
                )
            active = [
                r["id"]
                for r in conn.execute(
                    f"SELECT id FROM jobs WHERE song_id = ? AND stage IN ({_ACTIVE_SQL})",  # noqa: S608
                    (song_id,),
                )
            ]
            for job_id in active:
                self._cancel_job(job_id)
            in_queue = conn.execute(
                "SELECT 1 FROM queue_items WHERE song_id = ? LIMIT 1", (song_id,)
            ).fetchone()
            conn.execute("DELETE FROM songs WHERE id = ?", (song_id,))  # cascata: letras, jobs, itens da fila
            if in_queue is not None:
                txn.queue_changed = True
                if player is not None:
                    player.reconcile(txn)
            for job_id in active:
                txn.emit("job.removed", {"id": job_id})
            txn.emit("library.updated", {"action": "removed", "song_id": song_id, "song": None})
        self._remove_files(song_id)

    def _cancel_job(self, job_id: str) -> None:
        """Pede ao JobRunner (T-006) para cancelar o job, se ele expuser `cancel(job_id)`."""
        runner = getattr(self.app.state, "runner", None) or getattr(self.app.state, "jobs", None)
        cancel = getattr(runner, "cancel", None)
        if cancel is None:
            return
        try:
            cancel(job_id)
        except Exception:  # noqa: BLE001
            log.exception("falha ao cancelar o job %s durante a exclusão da música", job_id)

    def _remove_files(self, song_id: str) -> None:
        config = self.app.state.config
        root = config.songs_dir.resolve()
        target = (root / song_id).resolve()
        if target.parent != root:  # song_id já é validado por regex; defesa extra contra path traversal
            return
        if target.exists():
            try:
                shutil.rmtree(target)
            except OSError:
                log.exception("não foi possível apagar %s", target)


def startup(app: FastAPI) -> None:
    state_lock(app)
    app.state.library = LibraryService(app)
