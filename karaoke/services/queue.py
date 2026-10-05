"""QueueService: CRUD/reordenação da fila (`sort_key` REAL), próximo item **pronto**, revisão da fila,
`added_by` vindo do principal e remoção via `security.ensure_can_remove` (contrato §8).

Interface para o JobRunner (T-006), segura para chamar de qualquer thread:
    app.state.queue.song_changed(song_id)
→ republica `queue.updated` se a música estiver na fila (ex.: ficou pronta) e deixa o player iniciar/recalcular
"A seguir". Além disso, o serviço escuta `job.updated`/`library.updated` publicados no EventBus e faz o mesmo
automaticamente (com ~250 ms de atraso, depois do COMMIT de quem publicou).

Dono: backend-engineer (T-005).
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Any

from fastapi import FastAPI

from karaoke.db import next_id, utcnow
from karaoke.errors import ApiError, invalid_request
from karaoke.events import Event
from karaoke.guest import policy
from karaoke.security import can_remove, ensure_can_remove
from karaoke.services.library import (
    ACTIVE_JOB_STAGES,
    load_song_row,
    song_not_found,
    song_to_dict,
    transaction,
)

HISTORY_LIMIT = 20
SINGER_MAX = 80
SORT_STEP = 1024.0


def item_not_found(item_id: str) -> ApiError:
    return ApiError(404, "queue_item_not_found", "Item da fila não encontrado.", {"queue_item_id": item_id})


def clean_singer(singer: str | None) -> str | None:
    if singer is None:
        return None
    singer = " ".join(singer.split())
    if len(singer) > SINGER_MAX:
        raise invalid_request("Nome do cantor muito longo.", {"singer": f"máximo {SINGER_MAX} caracteres"})
    return singer or None


class QueueService:
    def __init__(self, app: FastAPI):
        self.app = app
        self._dirty: set[str] = set()
        self._dirty_lock = threading.Lock()
        self._job_stage: dict[str, str] = {}

    @property
    def db(self):  # noqa: ANN201
        return self.app.state.db

    # ---- leitura / serialização ----------------------------------------------------------------
    def item_dict(self, conn: sqlite3.Connection, row: sqlite3.Row, actor: str) -> dict[str, Any]:
        song_row = load_song_row(conn, row["song_id"])
        song = song_to_dict(song_row, actor) if song_row is not None else None
        job = None
        if song is not None and song["job_id"]:
            j = conn.execute("SELECT stage, progress FROM jobs WHERE id = ?", (song["job_id"],)).fetchone()
            if j is not None:
                job = {"stage": j["stage"], "progress": j["progress"]}
        item = {
            "id": row["id"],
            "song_id": row["song_id"],
            "singer": row["singer"],
            "status": row["status"],
            "ready": bool(song and song["status"] == "ready"),
            "song": song,
            "job": job,
            "added_by": row["added_by"],
            "added_by_name": row["added_by_name"],
            "can_remove": False,
            "added_at": row["added_at"],
        }
        item["can_remove"] = can_remove(actor, item)
        return item

    def revision(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT value FROM meta WHERE key = 'queue_revision'").fetchone()
        return int(row["value"]) if row else 0

    def bump_revision(self, conn: sqlite3.Connection) -> int:
        rev = self.revision(conn) + 1
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('queue_revision', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(rev),),
        )
        return rev

    def pending_rows(self, conn: sqlite3.Connection) -> list[sqlite3.Row]:
        return conn.execute(
            "SELECT * FROM queue_items WHERE status = 'pending' ORDER BY sort_key, added_at, rowid"
        ).fetchall()

    def get_row(self, conn: sqlite3.Connection, item_id: str) -> sqlite3.Row | None:
        return conn.execute("SELECT * FROM queue_items WHERE id = ?", (item_id,)).fetchone()

    def snapshot_conn(self, conn: sqlite3.Connection, actor: str = "host") -> dict[str, Any]:
        current = conn.execute(
            "SELECT * FROM queue_items WHERE status = 'playing' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        history = conn.execute(
            "SELECT * FROM queue_items WHERE status IN ('done','skipped') "
            "ORDER BY finished_at DESC, rowid DESC LIMIT ?",
            (HISTORY_LIMIT,),
        ).fetchall()
        return {
            "revision": self.revision(conn),
            "current": self.item_dict(conn, current, actor) if current is not None else None,
            "items": [self.item_dict(conn, r, actor) for r in self.pending_rows(conn)],
            "history": [self.item_dict(conn, r, actor) for r in history],
        }

    def snapshot(self, actor: str = "host") -> dict[str, Any]:
        conn = self.db.connect()
        try:
            return self.snapshot_conn(conn, actor)
        finally:
            conn.close()

    def get_item(self, item_id: str, actor: str) -> dict[str, Any]:
        conn = self.db.connect()
        try:
            row = self.get_row(conn, item_id)
            if row is None:
                raise item_not_found(item_id)
            return self.item_dict(conn, row, actor)
        finally:
            conn.close()

    def publish(self) -> None:
        self.app.state.bus.publish("queue.updated", self.snapshot("host"))

    # ---- helpers usados pelo player ---------------------------------------------------------------
    def next_ready(self, conn: sqlite3.Connection, exclude: str | None = None) -> sqlite3.Row | None:
        """Primeiro item pendente cuja música está pronta (itens não prontos são pulados, mas ficam na fila)."""
        return conn.execute(
            "SELECT q.* FROM queue_items q JOIN songs s ON s.id = q.song_id "
            "WHERE q.status = 'pending' AND s.status = 'ready' AND q.id IS NOT ? "
            "ORDER BY q.sort_key, q.added_at, q.rowid LIMIT 1",
            (exclude,),
        ).fetchone()

    def set_status(self, conn: sqlite3.Connection, item_id: str, status: str) -> None:
        finished = utcnow() if status in ("done", "skipped") else None
        conn.execute(
            "UPDATE queue_items SET status = ?, finished_at = ? WHERE id = ?", (status, finished, item_id)
        )

    def write_order(self, conn: sqlite3.Connection, ids: list[str]) -> None:
        for i, item_id in enumerate(ids):
            conn.execute("UPDATE queue_items SET sort_key = ? WHERE id = ?", ((i + 1) * SORT_STEP, item_id))

    def put_front(self, conn: sqlite3.Connection, item_id: str) -> None:
        """Volta um item a `pending` no topo da fila (usado por `prev`)."""
        conn.execute("UPDATE queue_items SET status = 'pending', finished_at = NULL WHERE id = ?", (item_id,))
        ids = [r["id"] for r in self.pending_rows(conn) if r["id"] != item_id]
        self.write_order(conn, [item_id, *ids])

    def _reconcile(self, txn: Any) -> None:
        player = getattr(self.app.state, "player", None)
        if player is not None:
            player.reconcile(txn)

    # ---- mutações (contrato §8) ----------------------------------------------------------------------
    def add(
        self, song_id: str, singer: str | None, position: Any, actor: str, principal: Any = None
    ) -> dict[str, Any]:
        """`principal` convidado (v0.3): só `end`, `singer` padrão = nome, `added_by_name` = nome da sessão.
        Sem cota por convidado (DECISION 0016)."""
        with transaction(self.app) as txn:
            return self.add_in_txn(txn, song_id, singer, position, actor, principal)

    def add_in_txn(
        self, txn: Any, song_id: str, singer: str | None, position: Any, actor: str, principal: Any = None
    ) -> dict[str, Any]:
        """Como `add`, dentro de uma `transaction(app)` já aberta (review-1 M-2: o `POST /api/jobs` de convidado
        cria o job e insere o item numa só transação). Os eventos saem no COMMIT de quem abriu."""
        added_by_name = None
        if policy.is_guest(principal):
            policy.ensure_position_end(principal, position)
            singer = policy.guest_singer(principal, singer)
            added_by_name = policy.guest_name(principal)
            actor = principal.id
        singer = clean_singer(singer)
        conn = txn.conn
        song = load_song_row(conn, song_id)
        if song is None:
            raise song_not_found(song_id)
        if song["status"] == "error":
            raise ApiError(
                409,
                "song_not_ready",
                "A música falhou no processamento. Tente processá-la de novo.",
                {"song_id": song_id},
            )
        item_id = f"q_{next_id(conn, 'queue_item')}"
        conn.execute(
            "INSERT INTO queue_items(id, song_id, singer, status, sort_key, added_by, added_by_name, added_at) "
            "VALUES (?, ?, ?, 'pending', 0, ?, ?, ?)",
            (item_id, song_id, singer, actor, added_by_name, utcnow()),
        )
        ids = [r["id"] for r in self.pending_rows(conn) if r["id"] != item_id]
        if position == "next":
            index = 0
        elif position == "end" or position is None:
            index = len(ids)
        else:
            index = max(0, min(int(position), len(ids)))
        ids.insert(index, item_id)
        self.write_order(conn, ids)
        txn.queue_changed = True
        self._reconcile(txn)
        return self.item_dict(conn, self.get_row(conn, item_id), actor)

    def update(self, item_id: str, singer: str | None, actor: str) -> dict[str, Any]:
        singer = clean_singer(singer)
        with transaction(self.app) as txn:
            conn = txn.conn
            row = self.get_row(conn, item_id)
            if row is None:
                raise item_not_found(item_id)
            conn.execute("UPDATE queue_items SET singer = ? WHERE id = ?", (singer, item_id))
            txn.queue_changed = True
            player = getattr(self.app.state, "player", None)
            if player is not None and player.involves_item(item_id):
                txn.player_changed = True
            return self.item_dict(conn, self.get_row(conn, item_id), actor)

    def remove(self, item_id: str, actor: str, principal: Any = None) -> None:
        with transaction(self.app) as txn:
            conn = txn.conn
            row = self.get_row(conn, item_id)
            if row is None:
                raise item_not_found(item_id)
            ensure_can_remove(actor, row, item_id)
            policy.ensure_guest_can_remove_item(principal, row, item_id)  # convidado: só `pending` (v0.3)
            player = getattr(self.app.state, "player", None)
            if row["status"] == "playing" and player is not None and player.involves_item(item_id):
                player.skip_current(txn)  # remover o item atual equivale a `next`
                return
            conn.execute("DELETE FROM queue_items WHERE id = ?", (item_id,))
            txn.queue_changed = True
            self._reconcile(txn)

    def clear(self) -> None:
        with transaction(self.app) as txn:
            cur = txn.conn.execute("DELETE FROM queue_items WHERE status = 'pending'")
            txn.queue_changed = True
            if cur.rowcount:
                self._reconcile(txn)

    def reorder(self, item_ids: list[str], revision: int | None, actor: str) -> dict[str, Any]:
        with transaction(self.app) as txn:
            conn = txn.conn
            current_rev = self.revision(conn)
            if revision is not None and revision != current_rev:
                raise ApiError(
                    409,
                    "stale_queue",
                    "A fila mudou enquanto você reorganizava. Atualize e tente de novo.",
                    {"revision": current_rev},
                )
            pending = [r["id"] for r in self.pending_rows(conn)]
            if len(item_ids) != len(set(item_ids)) or set(item_ids) != set(pending):
                raise invalid_request(
                    "A lista não corresponde aos itens pendentes da fila.",
                    {"item_ids": "deve conter exatamente os itens pendentes"},
                )
            self.write_order(conn, item_ids)
            txn.queue_changed = True
            self._reconcile(txn)
        return self.snapshot(actor)

    def move(self, item_id: str, to_index: int | None, direction: str | None, actor: str) -> dict[str, Any]:
        with transaction(self.app) as txn:
            conn = txn.conn
            row = self.get_row(conn, item_id)
            if row is None:
                raise item_not_found(item_id)
            if row["status"] != "pending":
                raise invalid_request(
                    "Só itens pendentes podem ser movidos.", {"item_id": "não está pendente"}
                )
            ids = [r["id"] for r in self.pending_rows(conn)]
            idx = ids.index(item_id)
            if to_index is not None:
                target = to_index
            else:
                target = {"up": idx - 1, "down": idx + 1, "top": 0, "bottom": len(ids) - 1}[direction or ""]
            target = max(0, min(target, len(ids) - 1))
            ids.pop(idx)
            ids.insert(target, item_id)
            self.write_order(conn, ids)
            txn.queue_changed = True
            self._reconcile(txn)
        return self.snapshot(actor)

    # ---- integração com jobs/biblioteca (T-006) ---------------------------------------------------------
    def song_changed(self, song_id: str) -> None:
        """A música mudou (status/job/letra). Republica a fila se ela estiver lá e deixa o player reagir."""
        with transaction(self.app) as txn:
            hit = txn.conn.execute(
                "SELECT 1 FROM queue_items WHERE song_id = ? LIMIT 1", (song_id,)
            ).fetchone()
            if hit is None:
                return
            txn.queue_changed = True
            self._reconcile(txn)

    def on_event(self, event: Event) -> None:
        """Listener do EventBus (roda na thread de quem publicou): só marca; o processamento é no tick."""
        data = event.data if isinstance(event.data, dict) else {}
        song_id = None
        if event.type == "library.updated":
            song_id = data.get("song_id")
        elif event.type == "job.updated":
            job_id, stage = data.get("id"), data.get("stage")
            terminal = stage not in ACTIVE_JOB_STAGES
            if job_id and (self._job_stage.get(job_id) != stage or terminal):
                song_id = data.get("song_id")
                if terminal:
                    self._job_stage.pop(job_id, None)
                else:
                    self._job_stage[job_id] = stage
        if song_id:
            with self._dirty_lock:
                self._dirty.add(song_id)

    def process_dirty(self) -> None:
        with self._dirty_lock:
            dirty, self._dirty = self._dirty, set()
        for song_id in dirty:
            self.song_changed(song_id)


def startup(app: FastAPI) -> None:
    svc = QueueService(app)
    app.state.queue = svc
    app.state.bus.add_listener(svc.on_event)
