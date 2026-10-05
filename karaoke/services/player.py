"""PlayerService: PlayerState único (ARCHITECTURE §4, contrato §9), comandos, reports do Palco dono do áudio,
auto-avanço com tela "A seguir", posse do áudio (stage claim: o último claim vence — DECISION 0006) e persistência
na tabela `player_state`.

Um laço assíncrono (`tick`, a cada 250 ms) cuida do que depende de tempo: fim do countdown de "A seguir",
Palco dono desconectado por > 5 s (pausa e libera a posse), mudança no nº de Palcos conectados e mudanças de
músicas sinalizadas pelo EventBus (fila).

Dono: backend-engineer (T-005).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

from fastapi import FastAPI

from karaoke.api.settings import read_settings, write_settings
from karaoke.db import utcnow
from karaoke.errors import ApiError, invalid_request
from karaoke.services.library import Txn, load_song, load_song_row, song_to_dict, transaction
from karaoke.services.queue import item_not_found

log = logging.getLogger("karaoke")

STATUSES = ("idle", "waiting_gesture", "loading", "playing", "paused", "up_next", "ended")
ACTIVE = ("waiting_gesture", "loading", "playing", "paused")  # há um item atual
IN_USE = ("loading", "playing", "paused")  # música não pode ser excluída
TICK_S = 0.25
POSITION_MIN_INTERVAL_MS = 250  # player.position ≤ 4 Hz
POSITION_PERSIST_MS = 5000
PREV_RESTART_MS = 3000
OFFSET_LIMIT_MS = 10000

ACTIONS = (
    "play",
    "pause",
    "toggle",
    "next",
    "prev",
    "restart",
    "seek",
    "seek_relative",
    "volume",
    "volume_relative",
    "guide_vocal",
    "guide_vocal_relative",
    "offset",
    "offset_relative",
    "autoplay",
    "start",
)
REPORT_EVENTS = ("tick", "loaded", "started", "paused", "seeked", "ended", "error")


def now_ms() -> int:
    return int(time.time() * 1000)


def stage_not_connected() -> ApiError:
    return ApiError(
        409, "stage_not_connected", "Nenhum Palco está tocando. Abra o Palco e pressione Enter.", None
    )


def song_not_ready(song_id: str | None = None) -> ApiError:
    return ApiError(
        409, "song_not_ready", "A música ainda não está pronta.", {"song_id": song_id} if song_id else None
    )


def _num(value: Any, field: str, *, integer: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise invalid_request("Valor inválido.", {"value": f"{field}: número obrigatório"})
    return int(value) if integer else float(value)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class PlayerService:
    OWNER_GRACE_MS = 5000  # Palco dono sem SSE por mais que isso → pausa e libera a posse

    def __init__(self, app: FastAPI):
        self.app = app
        self.s: dict[str, Any] = {}
        self.revision = 0
        self._last_position_pub = 0
        self._last_position_save = 0
        self._owner_missing_since: int | None = None
        self._stage_count = 0
        self._task: asyncio.Task | None = None

    # ---- infraestrutura -----------------------------------------------------------------------------
    @property
    def db(self):  # noqa: ANN201
        return self.app.state.db

    @property
    def bus(self):  # noqa: ANN201
        return self.app.state.bus

    @property
    def queue(self):  # noqa: ANN201
        return self.app.state.queue

    def _settings(self, conn) -> dict[str, Any]:  # noqa: ANN001
        return read_settings(conn, self.app.state.config)

    def load(self) -> None:
        """Lê o estado persistido. Após reinício nenhum Palco está conectado: pausa e libera a posse."""
        with transaction(self.app) as txn:
            conn = txn.conn
            settings = self._settings(conn)
            row = conn.execute("SELECT revision, state_json FROM player_state WHERE id = 1").fetchone()
            saved = json.loads(row["state_json"]) if row else {}
            self.revision = int(row["revision"]) if row else 0
            self.s = {
                "status": "idle",
                "queue_item_id": None,
                "song_id": None,
                "position_ms": 0,
                "position_at": now_ms(),
                "duration_ms": None,
                "volume": settings["volume"],
                "guide_vocal": settings["guide_vocal_default"],
                "autoplay": settings["autoplay"],
                "up_next": None,
                "audio_owner": None,
                "unlocked": False,
            }
            self.s.update({k: v for k, v in saved.items() if k in self.s})
            self.s.update(
                volume=settings["volume"],
                guide_vocal=settings["guide_vocal_default"],
                autoplay=settings["autoplay"],
                audio_owner=None,
                unlocked=False,
                position_at=now_ms(),
            )
            if self.s["status"] not in STATUSES:
                self.s["status"] = "idle"
            if self.s["status"] in ("loading", "playing"):
                self.s["status"] = "paused"
            if self.s["status"] == "up_next" and self.s["up_next"]:
                self.s["up_next"]["starts_at"] = None
            # coerência com a fila: item atual tem de existir e estar 'playing'
            if self.s["status"] in ACTIVE:
                item = self.queue.get_row(conn, self.s["queue_item_id"] or "")
                if item is None:
                    self._clear_current()
                    self.s["status"] = "idle"
            conn.execute(
                "UPDATE queue_items SET status = 'pending' WHERE status = 'playing' AND id IS NOT ?",
                (self.s["queue_item_id"] if self.s["status"] in ACTIVE else None,),
            )
            self.reconcile(txn)
            txn.player_changed = True

    def save(self, conn) -> None:  # noqa: ANN001
        self.revision += 1
        conn.execute(
            "INSERT INTO player_state(id, revision, state_json, updated_at) VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET revision = excluded.revision, state_json = excluded.state_json, "
            "updated_at = excluded.updated_at",
            (self.revision, json.dumps(self.s), utcnow()),
        )
        self._last_position_save = now_ms()

    def publish(self) -> None:
        self.bus.publish("player.state", self.state("host"))

    # ---- leitura ----------------------------------------------------------------------------------------
    def state_conn(self, conn, actor: str = "host") -> dict[str, Any]:  # noqa: ANN001
        s = self.s
        song = load_song(conn, s["song_id"], actor) if s["song_id"] else None
        singer = None
        if s["queue_item_id"]:
            item = self.queue.get_row(conn, s["queue_item_id"])
            singer = item["singer"] if item is not None else None
        up_next = None
        if s["up_next"]:
            nxt = self.queue.get_row(conn, s["up_next"]["queue_item_id"])
            if nxt is not None:
                up_next = {
                    "queue_item_id": nxt["id"],
                    "song": load_song(conn, nxt["song_id"], actor),
                    "singer": nxt["singer"],
                    "added_by_name": nxt["added_by_name"],
                    "starts_at": s["up_next"]["starts_at"],
                }
        return {
            "revision": self.revision,
            "status": s["status"],
            "queue_item_id": s["queue_item_id"],
            "song_id": s["song_id"],
            "song": song,
            "singer": singer,
            "position_ms": int(s["position_ms"]),
            "position_at": int(s["position_at"]),
            "duration_ms": s["duration_ms"],
            "volume": s["volume"],
            "guide_vocal": s["guide_vocal"],
            "offset_ms": song["offset_ms"] if song else 0,
            "autoplay": s["autoplay"],
            "up_next": up_next,
            "stage": {
                "connected": self.bus.count("stage"),
                "audio_owner": s["audio_owner"],
                "unlocked": bool(s["unlocked"]),
            },
        }

    def state(self, actor: str = "host") -> dict[str, Any]:
        conn = self.db.connect()
        try:
            return self.state_conn(conn, actor)
        finally:
            conn.close()

    def involves_song(self, song_id: str) -> bool:
        s = self.s
        if s.get("song_id") == song_id:
            return True
        return bool(s.get("up_next")) and self._up_next_song_id() == song_id

    def involves_item(self, item_id: str) -> bool:
        s = self.s
        return s.get("queue_item_id") == item_id or bool(
            s.get("up_next") and s["up_next"].get("queue_item_id") == item_id
        )

    def song_in_use(self, song_id: str) -> bool:
        return self.s.get("status") in IN_USE and self.s.get("song_id") == song_id

    def _up_next_song_id(self) -> str | None:
        conn = self.db.connect()
        try:
            row = self.queue.get_row(conn, self.s["up_next"]["queue_item_id"])
            return row["song_id"] if row else None
        finally:
            conn.close()

    def position(self, at: int | None = None) -> int:
        """Posição estimada agora (extrapolada se tocando)."""
        s = self.s
        pos = int(s["position_ms"])
        if s["status"] == "playing":
            pos += max(0, (at or now_ms()) - int(s["position_at"]))
        if s["duration_ms"]:
            pos = min(pos, int(s["duration_ms"]))
        return max(0, pos)

    # ---- transições internas (sempre dentro de `transaction`) ------------------------------------------
    def _set_position(self, pos: int) -> None:
        self.s["position_ms"] = max(0, int(pos))
        self.s["position_at"] = now_ms()

    def _clear_current(self) -> None:
        self.s.update(queue_item_id=None, song_id=None, duration_ms=None, position_ms=0, position_at=now_ms())

    def _start_item(self, txn: Txn, item) -> None:  # noqa: ANN001
        conn = txn.conn
        conn.execute(
            "UPDATE queue_items SET status = 'pending' WHERE status = 'playing' AND id != ?", (item["id"],)
        )
        conn.execute(
            "UPDATE queue_items SET status = 'playing', finished_at = NULL WHERE id = ?", (item["id"],)
        )
        song = load_song_row(conn, item["song_id"])
        self.s.update(
            queue_item_id=item["id"],
            song_id=item["song_id"],
            duration_ms=song["duration_ms"] if song is not None else None,
            up_next=None,
            status="loading" if self.s["audio_owner"] else "waiting_gesture",
        )
        self._set_position(0)
        txn.queue_changed = txn.player_changed = True

    def _finish_current(self, txn: Txn, status: str) -> None:
        if self.s["queue_item_id"]:
            self.queue.set_status(txn.conn, self.s["queue_item_id"], status)
            txn.queue_changed = True
        self._clear_current()
        txn.player_changed = True

    def _advance(self, txn: Txn, countdown: bool) -> None:
        """Depois de terminar/pular o atual: próximo item pronto (com ou sem "A seguir") ou `ended`."""
        conn = txn.conn
        nxt = self.queue.next_ready(conn)
        txn.player_changed = True
        if nxt is None:
            self.s.update(status="ended", up_next=None)
            return
        settings = self._settings(conn)
        cd = int(settings["countdown_s"])
        if countdown and (cd > 0 or not self.s["autoplay"]):
            starts_at = now_ms() + cd * 1000 if self.s["autoplay"] else None
            self.s.update(status="up_next", up_next={"queue_item_id": nxt["id"], "starts_at": starts_at})
        else:
            self._start_item(txn, nxt)

    def skip_current(self, txn: Txn) -> None:
        self._finish_current(txn, "skipped")
        self._advance(txn, countdown=False)

    def reconcile(self, txn: Txn) -> None:
        """Reage a mudanças na fila: autoinício em idle/ended, recalcula "A seguir", item atual removido."""
        conn = txn.conn
        s = self.s
        if s["status"] in ACTIVE and s["queue_item_id"]:
            item = self.queue.get_row(conn, s["queue_item_id"])
            if item is None or item["status"] != "playing":
                self._clear_current()
                self._advance(txn, countdown=False)
                return
        if s["status"] in ("idle", "ended") and s["autoplay"]:
            nxt = self.queue.next_ready(conn)
            if nxt is not None:
                self._start_item(txn, nxt)
        elif s["status"] == "up_next":
            nxt = self.queue.next_ready(conn)
            if nxt is None:
                s.update(status="ended", up_next=None)
                txn.player_changed = True
            elif nxt["id"] != (s["up_next"] or {}).get("queue_item_id"):
                starts_at = (s["up_next"] or {}).get("starts_at")
                s["up_next"] = {"queue_item_id": nxt["id"], "starts_at": starts_at}
                txn.player_changed = True

    def apply_settings(self, txn: Txn, patch: dict[str, Any]) -> None:
        """Settings mudaram via PATCH: reflete volume/autoplay/voz guia no estado do player."""
        mapping = {"volume": "volume", "autoplay": "autoplay", "guide_vocal_default": "guide_vocal"}
        for key, field in mapping.items():
            if key in patch and self.s.get(field) != patch[key]:
                self.s[field] = patch[key]
                txn.player_changed = True
        if "autoplay" in patch:
            self._autoplay_changed(txn)

    def _autoplay_changed(self, txn: Txn) -> None:
        s = self.s
        if s["status"] == "up_next" and s["up_next"]:
            if s["autoplay"] and s["up_next"]["starts_at"] is None:
                cd = int(self._settings(txn.conn)["countdown_s"])
                s["up_next"]["starts_at"] = now_ms() + cd * 1000
            elif not s["autoplay"]:
                s["up_next"]["starts_at"] = None
            txn.player_changed = True
        elif s["autoplay"]:
            self.reconcile(txn)

    def _persist_setting(self, txn: Txn, key: str, value: Any) -> None:
        write_settings(txn.conn, {key: value})
        txn.emit("settings.updated", self._settings(txn.conn))

    def _require_owner(self, txn: Txn) -> None:
        if not self.s["audio_owner"]:
            if self.s["status"] in ("paused", "loading"):
                self.s["status"] = "waiting_gesture"
                txn.player_changed = True
            raise _Deferred(stage_not_connected())

    # ---- comandos (contrato §9) -------------------------------------------------------------------------
    def command(self, action: str, value: Any = None, actor: str = "host") -> dict[str, Any]:
        if action not in ACTIONS:
            raise invalid_request("Ação desconhecida.", {"action": "inválida"})
        deferred: ApiError | None = None
        with transaction(self.app) as txn:
            try:
                getattr(self, f"_cmd_{action}")(txn, value)
            except _Deferred as d:  # erro que não desfaz a mudança de estado (ex.: waiting_gesture + 409)
                deferred = d.error
        if deferred is not None:
            raise deferred
        return self.state(actor)

    def _cmd_play(self, txn: Txn, _: Any) -> None:
        s = self.s
        st = s["status"]
        if st in ("idle", "ended"):
            nxt = self.queue.next_ready(txn.conn)
            if nxt is None:
                return
            self._start_item(txn, nxt)
            if not s["audio_owner"]:
                raise _Deferred(stage_not_connected())
        elif st == "up_next":
            self._start_up_next(txn)
            if not s["audio_owner"]:
                raise _Deferred(stage_not_connected())
        elif st in ("paused", "waiting_gesture"):
            self._require_owner(txn)
            if st == "waiting_gesture":
                s["status"] = "loading"
            else:
                s["status"] = "playing"
                self._set_position(s["position_ms"])
            txn.player_changed = True

    def _cmd_pause(self, txn: Txn, _: Any) -> None:
        s = self.s
        if s["status"] in ("playing", "loading"):
            pos = self.position()
            s["status"] = "paused"
            self._set_position(pos)
            txn.player_changed = True
        elif s["status"] == "up_next" and s["up_next"] and s["up_next"]["starts_at"] is not None:
            s["up_next"]["starts_at"] = None  # congela o countdown; `play` começa a próxima
            txn.player_changed = True

    def _cmd_toggle(self, txn: Txn, value: Any) -> None:
        if self.s["status"] in ("playing", "loading"):
            self._cmd_pause(txn, value)
        else:
            self._cmd_play(txn, value)

    def _cmd_next(self, txn: Txn, _: Any) -> None:
        if self.s["status"] == "up_next":
            self._start_up_next(txn)
            return
        if self.s["status"] in ACTIVE:
            self.skip_current(txn)
        elif self.s["status"] in ("idle", "ended"):
            nxt = self.queue.next_ready(txn.conn)
            if nxt is not None:
                self._start_item(txn, nxt)

    def _cmd_prev(self, txn: Txn, _: Any) -> None:
        s = self.s
        if s["status"] in ACTIVE and self.position() > PREV_RESTART_MS:
            self._set_position(0)
            txn.player_changed = True
            return
        conn = txn.conn
        prev = conn.execute(
            "SELECT q.* FROM queue_items q JOIN songs so ON so.id = q.song_id "
            "WHERE q.status IN ('done','skipped') AND so.status = 'ready' "
            "ORDER BY q.finished_at DESC, q.rowid DESC LIMIT 1"
        ).fetchone()
        if prev is None:
            if s["status"] in ACTIVE:
                self._set_position(0)
                txn.player_changed = True
            return
        if s["status"] in ACTIVE and s["queue_item_id"]:
            self.queue.put_front(conn, s["queue_item_id"])  # o atual volta a ser o próximo
        self._start_item(txn, prev)

    def _cmd_restart(self, txn: Txn, _: Any) -> None:
        if self.s["status"] in ACTIVE:
            self._set_position(0)
            txn.player_changed = True

    def _cmd_seek(self, txn: Txn, value: Any) -> None:
        target = _num(value, "seek", integer=True)
        self._seek(txn, int(target))

    def _cmd_seek_relative(self, txn: Txn, value: Any) -> None:
        delta = _num(value, "seek_relative", integer=True)
        self._seek(txn, self.position() + int(delta))

    def _seek(self, txn: Txn, target: int) -> None:
        if self.s["status"] not in ACTIVE:
            return
        hi = self.s["duration_ms"] or max(target, 0)
        self._set_position(int(_clamp(target, 0, hi)))
        txn.player_changed = True

    def _set_level(self, txn: Txn, field: str, setting: str, value: float) -> None:
        value = round(_clamp(value, 0.0, 1.0), 4)
        if self.s[field] != value:
            self.s[field] = value
            txn.player_changed = True
            self._persist_setting(txn, setting, value)

    def _cmd_volume(self, txn: Txn, value: Any) -> None:
        self._set_level(txn, "volume", "volume", _num(value, "volume"))

    def _cmd_volume_relative(self, txn: Txn, value: Any) -> None:
        self._set_level(txn, "volume", "volume", self.s["volume"] + _num(value, "volume_relative"))

    def _cmd_guide_vocal(self, txn: Txn, value: Any) -> None:
        self._set_level(txn, "guide_vocal", "guide_vocal_default", _num(value, "guide_vocal"))

    def _cmd_guide_vocal_relative(self, txn: Txn, value: Any) -> None:
        delta = _num(value, "guide_vocal_relative")
        self._set_level(txn, "guide_vocal", "guide_vocal_default", self.s["guide_vocal"] + delta)

    def _set_offset(self, txn: Txn, value: int) -> None:
        song_id = self.s["song_id"]
        if not song_id:
            raise invalid_request(
                "Nenhuma música atual para ajustar o offset.", {"action": "sem música atual"}
            )
        value = int(_clamp(value, -OFFSET_LIMIT_MS, OFFSET_LIMIT_MS))
        conn = txn.conn
        conn.execute(
            "UPDATE songs SET offset_ms = ?, updated_at = ? WHERE id = ?", (value, utcnow(), song_id)
        )
        txn.player_changed = True
        song = load_song(conn, song_id)
        if song is not None:
            txn.emit("song.updated", song)
            if conn.execute("SELECT 1 FROM queue_items WHERE song_id = ? LIMIT 1", (song_id,)).fetchone():
                txn.queue_changed = True

    def _current_offset(self, conn) -> int:  # noqa: ANN001
        row = conn.execute("SELECT offset_ms FROM songs WHERE id = ?", (self.s["song_id"] or "",)).fetchone()
        return int(row["offset_ms"]) if row else 0

    def _cmd_offset(self, txn: Txn, value: Any) -> None:
        self._set_offset(txn, int(_num(value, "offset", integer=True)))

    def _cmd_offset_relative(self, txn: Txn, value: Any) -> None:
        delta = int(_num(value, "offset_relative", integer=True))
        self._set_offset(txn, self._current_offset(txn.conn) + delta)

    def _cmd_autoplay(self, txn: Txn, value: Any) -> None:
        if not isinstance(value, bool):
            raise invalid_request("Valor inválido.", {"value": "autoplay: true ou false"})
        if self.s["autoplay"] != value:
            self.s["autoplay"] = value
            txn.player_changed = True
            self._persist_setting(txn, "autoplay", value)
            self._autoplay_changed(txn)

    def _cmd_start(self, txn: Txn, value: Any) -> None:
        if not isinstance(value, str) or not value.startswith("q_") or not value[2:].isdigit():
            raise invalid_request("Valor inválido.", {"value": "start: queue_item_id (q_<n>)"})
        conn = txn.conn
        item = self.queue.get_row(conn, value)
        if item is None:
            raise item_not_found(value)
        song = load_song_row(conn, item["song_id"])
        if song is None or song["status"] != "ready":
            raise song_not_ready(item["song_id"])
        if self.s["queue_item_id"] == value and self.s["status"] in ACTIVE:
            self._cmd_restart(txn, None)  # `start` do item atual = reiniciar (o Palco não recarrega a faixa)
            return
        if self.s["status"] in ACTIVE:
            self._finish_current(txn, "skipped")
        self._start_item(txn, item)
        if not self.s["audio_owner"]:
            raise _Deferred(stage_not_connected())

    def _start_up_next(self, txn: Txn) -> None:
        up = self.s["up_next"] or {}
        item = self.queue.get_row(txn.conn, up.get("queue_item_id") or "")
        song = load_song_row(txn.conn, item["song_id"]) if item is not None else None
        if item is None or item["status"] != "pending" or song is None or song["status"] != "ready":
            item = self.queue.next_ready(txn.conn)
        if item is None:
            self.s.update(status="ended", up_next=None)
            txn.player_changed = True
            return
        self._start_item(txn, item)

    # ---- Palco: claim e reports -------------------------------------------------------------------------
    def claim(self, client_id: str, actor: str = "host") -> dict[str, Any]:
        with transaction(self.app) as txn:
            s = self.s
            s["audio_owner"] = client_id
            s["unlocked"] = True
            self._owner_missing_since = None if self.bus.connected(client_id, "stage") else now_ms()
            if s["status"] == "waiting_gesture":
                s["status"] = "loading"
            txn.player_changed = True
        return self.state(actor)

    def report(self, body: dict[str, Any]) -> None:
        """Report do Palco. Ignora (sem erro) reports de quem não é o dono ou de um item que não é o atual."""
        s = self.s
        if not s["audio_owner"] or body.get("client_id") != s["audio_owner"]:
            return
        if body.get("queue_item_id") != s["queue_item_id"] or s["status"] not in ACTIVE:
            return
        event = body.get("event", "tick")
        pos = int(body.get("position_ms") or 0)
        if event in ("tick", "seeked", "paused", "started"):
            with self.app.state.state_lock:
                if body.get("queue_item_id") != s["queue_item_id"]:
                    return
                if s["status"] == "loading" and event == "started":
                    with transaction(self.app) as txn:
                        s["status"] = "playing"
                        self._set_position(pos)
                        txn.player_changed = True
                    return
                s["position_ms"] = max(0, pos)
                s["position_at"] = now_ms()
                if now_ms() - self._last_position_save > POSITION_PERSIST_MS:
                    with self.app.state.db.tx() as conn:
                        self.save_position(conn)
            self._publish_position(bool(body.get("playing")), force=event != "tick")
            return
        with transaction(self.app) as txn:
            if body.get("queue_item_id") != s["queue_item_id"]:
                return
            if event == "loaded":
                # Só um item que está começando (`loading`) adota a posição do Palco. Em `playing`/`paused`
                # (outro Palco assumiu o som ou o Palco recarregou) a posição do servidor prevalece, para o
                # Palco retomar de onde a música estava (DECISION 0006).
                if s["status"] == "loading":
                    s["status"] = "playing"
                    self._set_position(pos)
                txn.player_changed = True
            elif event == "ended":
                self._finish_current(txn, "done")
                self._advance(txn, countdown=True)
            elif event == "error":
                err = body.get("error") or {}
                log.warning("Palco não conseguiu tocar %s: %s", s["song_id"], err)
                title = None
                if s["song_id"]:
                    song = load_song(txn.conn, s["song_id"])
                    title = song["title"] if song else None
                txn.emit(
                    "toast",
                    {
                        "level": "error",
                        "message": f"Não foi possível tocar “{title}”. Pulando para a próxima."
                        if title
                        else "Não foi possível tocar a música. Pulando para a próxima.",
                    },
                )
                self.skip_current(txn)

    def save_position(self, conn) -> None:  # noqa: ANN001
        """Persiste só a posição (sem nova revisão/evento)."""
        conn.execute(
            "UPDATE player_state SET state_json = ?, updated_at = ? WHERE id = 1",
            (json.dumps(self.s), utcnow()),
        )
        self._last_position_save = now_ms()

    def _publish_position(self, playing: bool, force: bool = False) -> None:
        t = now_ms()
        if not force and t - self._last_position_pub < POSITION_MIN_INTERVAL_MS:
            return
        self._last_position_pub = t
        s = self.s
        self.bus.publish(
            "player.position",
            {
                "queue_item_id": s["queue_item_id"],
                "position_ms": int(s["position_ms"]),
                "position_at": int(s["position_at"]),
                "playing": playing,
            },
        )

    # ---- laço de tempo ------------------------------------------------------------------------------------
    def tick(self) -> None:
        self.queue.process_dirty()
        t = now_ms()
        s = self.s
        owner = s["audio_owner"]
        if owner and self.bus.connected(owner, "stage"):
            self._owner_missing_since = None
        elif owner and self._owner_missing_since is None:
            self._owner_missing_since = t
        due = s["status"] == "up_next" and bool(s["up_next"]) and s["up_next"]["starts_at"] is not None
        due = due and t >= int(s["up_next"]["starts_at"])
        owner_lost = (
            bool(owner)
            and self._owner_missing_since is not None
            and t - self._owner_missing_since > self.OWNER_GRACE_MS
        )
        count = self.bus.count("stage")
        if not (due or owner_lost or count != self._stage_count):
            return
        with transaction(self.app) as txn:
            up = s["up_next"] or {}
            due = due and up.get("starts_at") is not None and now_ms() >= int(up["starts_at"])
            if due and s["status"] == "up_next":  # fim do countdown de "A seguir" (rechecado sob o lock)
                self._start_up_next(txn)
            if owner_lost and s["audio_owner"] == owner:  # Palco dono sumiu por > OWNER_GRACE_MS
                log.info("Palco %s desconectado; pausando", owner)
                if s["status"] in ("playing", "loading"):
                    pos = self.position(t)
                    s["status"] = "paused"
                    self._set_position(pos)
                s["audio_owner"] = None
                s["unlocked"] = False
                self._owner_missing_since = None
                txn.player_changed = True
            if count != self._stage_count:  # nº de Palcos conectados mudou
                self._stage_count = count
                txn.player_changed = True

    async def run(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            try:
                await asyncio.to_thread(self.tick)
            except Exception:  # noqa: BLE001
                log.exception("erro no laço do player")


class _Deferred(Exception):  # noqa: N818
    def __init__(self, error: ApiError):
        super().__init__(error.message)
        self.error = error


def startup(app: FastAPI) -> None:
    svc = PlayerService(app)
    app.state.player = svc
    svc.load()
    svc._stage_count = app.state.bus.count("stage")
    svc._task = asyncio.get_running_loop().create_task(svc.run())


async def shutdown(app: FastAPI) -> None:
    svc: PlayerService | None = getattr(app.state, "player", None)
    if svc is None or svc._task is None:
        return
    svc._task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await svc._task
    with contextlib.suppress(Exception), app.state.db.tx() as conn:
        svc.save_position(conn)


__all__ = ["PlayerService", "song_to_dict", "startup", "shutdown"]
