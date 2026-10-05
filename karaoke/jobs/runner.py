"""JobRunner: fila serial persistida (tabela jobs), uma thread, etapas e pesos de progresso (ARCHITECTURE §3),
cancelamento, retomada após reinício, publicação de job.updated no EventBus.

Interface para outros módulos (thread-safe):
    runner = app.state.runner
    runner.insert_job(conn, kind, ...) + runner.notify_created(job_id)  (kinds: process | transcribe | align |
                                   analyze — usadas pela API de jobs/letras)
    runner.cancel(job_id)          só sinaliza (não escreve no banco) — seguro dentro de transações alheias
    runner.active_jobs()           lista de Job (contrato §5) para o `hello` do SSE
    runner.job_dict(job_id)        Job | None

Escritas no banco são sempre feitas pela thread do runner ou pela rota que recebeu a requisição; arquivos
parciais ficam em data/tmp/<job>/ e só chegam a data/songs/<id>/ por `rename` atômico no fim.

Dono: backend-engineer (T-006).
"""

from __future__ import annotations

import contextlib
import errno
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from karaoke import lrclib, system
from karaoke import youtube as yt
from karaoke.config import PROJECT_DIR, Config
from karaoke.db import Database, next_id, utcnow
from karaoke.jobs import models
from karaoke.lyrics import align, builder
from karaoke.lyrics.titleclean import clean_channel

log = logging.getLogger("karaoke.jobs")

ACTIVE_STAGES = ("queued", "model_download", "downloading", "separating", "lyrics", "finalizing")
RUNNING_STAGES = ACTIVE_STAGES[1:]
TERMINAL_STAGES = ("done", "error", "canceled")
MAX_RESTART_ATTEMPTS = 3
PUBLISH_INTERVAL_S = 0.25  # job.updated ≤ 4 Hz por job
RECENT_HOURS = 24

# faixas de progresso global (ARCHITECTURE §3)
PROCESS_RANGES = {
    "model_download": (0.00, 0.10),
    "downloading": (0.10, 0.30),
    "separating": (0.30, 0.80),
    "lyrics": (0.80, 0.95),
    "finalizing": (0.95, 1.00),
}
TRANSCRIBE_RANGES = {"model_download": (0.0, 0.10), "lyrics": (0.10, 1.0)}

MESSAGES = {
    "network_unavailable": "Sem conexão com a internet. Verifique a rede e tente de novo.",
    "youtube_error": "Não foi possível baixar o vídeo.",
    "video_unavailable": "Este vídeo não está disponível para download.",
    "download_failed": "O download do áudio falhou.",
    "ffmpeg_failed": "Falha ao converter o áudio (ffmpeg).",
    "model_download_failed": "Não foi possível baixar o modelo de IA (sem internet?).",
    "separation_failed": "Falha ao separar a voz da música.",
    "gpu_out_of_memory": "A GPU ficou sem memória. Feche outros programas que usam a GPU e tente de novo.",
    "transcription_failed": "Falha ao transcrever a letra.",
    "disk_full": "Disco cheio. Libere espaço e tente de novo.",
    "interrupted": "O processamento foi interrompido várias vezes (o app foi fechado). Tente de novo.",
    "canceled": "Cancelado.",
    "internal_error": "Erro inesperado no processamento. Veja o log em data/logs/karaoke.log.",
    "align_failed": "Não foi possível sincronizar a letra pela voz.",
    "video_too_long": "O vídeo é mais longo do que o permitido para pedidos de convidados.",
}

LYRICS_MODES = ("aligned", "transcribe", "online")
LEGACY_MODES = {"transcribe": "aligned"}  # CR-001
MAX_TEXT_BASES = 3  # letras oficiais testadas no alinhamento antes de cair no texto do Whisper
DEFAULT_LYRICS_MODE = "aligned"
MIN_ALIGN_COVERAGE = 0.3  # abaixo disso a letra online provavelmente não é desta gravação → fica a do Whisper
TRANSCRIPT_FILE = "transcript.json"  # cache da transcrição (segmentos + envelope) em data/songs/<id>/


class JobError(Exception):
    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or MESSAGES.get(code, code))
        self.code = code
        self.message = message or MESSAGES.get(code, code)


class JobCanceled(Exception):
    pass


class _NotQueued(Exception):  # noqa: N818
    """O job saiu de `queued` (apagado/cancelado) antes de começar."""


class _Ctx:
    """Estado em memória do job em execução."""

    def __init__(self, job_id: str, kind: str, song_id: str):
        self.job_id = job_id
        self.kind = kind
        self.song_id = song_id
        self.cancel = threading.Event()
        self.proc: subprocess.Popen | None = None
        self.ranges = PROCESS_RANGES if kind == "process" else TRANSCRIBE_RANGES
        self.stage = "queued"
        self.last_publish = 0.0

    def check(self) -> None:
        if self.cancel.is_set():
            raise JobCanceled()


def _queue_key(row: sqlite3.Row) -> tuple[int, int]:
    """Ordem da fila de processamento: `queue_seq` (renovado no retry → vai para o fim) e depois o id."""
    try:
        seq = int(json.loads(row["options_json"] or "{}").get("queue_seq") or 0)
    except (ValueError, TypeError):
        seq = 0
    return (seq or _job_id_num(row["id"]), _job_id_num(row["id"]))


def _job_id_num(job_id: str) -> int:
    try:
        return int(job_id.split("_", 1)[1])
    except (IndexError, ValueError):
        return 0


class JobRunner:
    def __init__(
        self,
        config: Config,
        db: Database,
        bus: Any,
        app: FastAPI | None = None,
        lrclib_factory: Callable[[], lrclib.LrclibClient] | None = None,
        autostart: bool = True,
    ):
        self.config = config
        self.db = db
        self.bus = bus
        self.app = app
        self._lrclib_factory = lrclib_factory
        self.autostart = autostart
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._current: _Ctx | None = None
        self._canceled_ids: set[str] = set()
        self._thread: threading.Thread | None = None
        self._device: tuple[str, str] | None = None  # (separação, whisper)
        self._gpu_warning: str | None = None
        self._last_sys = 0.0
        self._lock_file: Any = None
        self._model_listener: Callable[[dict[str, Any] | None], None] | None = None

    # ==== ciclo de vida ======================================================================
    def _acquire_lock(self) -> bool:
        """Um runner por diretório de dados (dois servidores no mesmo data/ executariam o mesmo job)."""
        try:
            import fcntl

            self.config.data_dir.mkdir(parents=True, exist_ok=True)
            self._lock_file = open(self.config.data_dir / "runner.lock", "w")  # noqa: SIM115
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except ImportError:
            return True
        except OSError:
            log.error(
                "outro processo do karaokê já usa %s; jobs não serão executados por este servidor",
                self.config.data_dir,
            )
            return False

    def start(self) -> None:
        if not self.autostart:
            self.recover()
            return
        if not self._acquire_lock():
            return
        self.recover()
        self._thread = threading.Thread(target=self._loop, name="job-runner", daemon=True)
        self._thread.start()
        self._wake.set()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            ctx = self._current
        if ctx is not None:
            # não marca como cancelado: o job volta a `queued` na próxima inicialização (retomada)
            self._kill(ctx)
        if self._thread is not None:
            self._thread.join(timeout)
        if self._lock_file is not None:
            with contextlib.suppress(OSError):
                self._lock_file.close()
            self._lock_file = None

    def recover(self) -> None:
        """Jobs que estavam rodando quando o app fechou voltam para a fila (ou erram após 3 tentativas)."""
        changed: list[str] = []
        with self.db.tx() as conn:
            rows = conn.execute(
                f"SELECT id, attempts, kind, song_id FROM jobs WHERE stage IN ({_in(RUNNING_STAGES)})"  # noqa: S608
            ).fetchall()
            now = utcnow()
            for r in rows:
                if r["attempts"] >= MAX_RESTART_ATTEMPTS:
                    conn.execute(
                        "UPDATE jobs SET stage='error', error_code='interrupted', error_message=?, "
                        "stage_progress=NULL, updated_at=?, finished_at=? WHERE id=?",
                        (MESSAGES["interrupted"], now, now, r["id"]),
                    )
                    if r["kind"] == "process":
                        conn.execute(
                            "UPDATE songs SET status='error', error_code='interrupted', updated_at=? "
                            "WHERE id=? AND status != 'ready'",
                            (now, r["song_id"]),
                        )
                else:
                    conn.execute(
                        "UPDATE jobs SET stage='queued', stage_progress=NULL, updated_at=? WHERE id=?",
                        (now, r["id"]),
                    )
                    if r["kind"] == "process":
                        conn.execute(
                            "UPDATE songs SET status='queued', updated_at=? WHERE id=? AND status != 'ready'",
                            (now, r["song_id"]),
                        )
                changed.append(r["id"])
        for jid in changed:
            log.info("job %s retomado após reinício", jid)
        # tmp órfão (job que não existe mais ou terminou)
        tmp = self.config.tmp_dir
        if tmp.is_dir():
            conn = self.db.connect()
            try:
                active = {
                    r["id"]
                    for r in conn.execute(f"SELECT id FROM jobs WHERE stage IN ({_in(ACTIVE_STAGES)})")  # noqa: S608
                }
            finally:
                conn.close()
            for d in tmp.iterdir():
                if d.is_dir() and d.name not in active:
                    shutil.rmtree(d, ignore_errors=True)

    # ==== consultas ==============================================================================
    def job_dict(self, job_id: str, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        own = conn is None
        conn = conn or self.db.connect()
        try:
            row = conn.execute(_JOB_SELECT + " WHERE j.id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            return self._to_dict(row, self._queue_positions(conn))
        finally:
            if own:
                conn.close()

    def list_jobs(self, status: str = "active") -> list[dict[str, Any]]:
        conn = self.db.connect()
        try:
            if status == "active":
                where = f"WHERE j.stage IN ({_in(ACTIVE_STAGES)})"
            elif status == "recent":
                where = (
                    f"WHERE j.stage IN ({_in(ACTIVE_STAGES)}) OR "
                    f"COALESCE(j.finished_at, j.updated_at) >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-{RECENT_HOURS} hours')"
                )
            else:
                where = ""
            rows = conn.execute(_JOB_SELECT + " " + where).fetchall()
            pos = self._queue_positions(conn)
        finally:
            conn.close()
        jobs = [self._to_dict(r, pos) for r in rows]

        def order(j: dict[str, Any]) -> tuple:
            if j["stage"] in RUNNING_STAGES:
                return (0, 0, "")
            if j["stage"] == "queued":
                return (1, j["queue_position"] or 0, "")
            return (2, 0, _invert(j["updated_at"] or ""))

        return sorted(jobs, key=order)

    def active_jobs(self) -> list[dict[str, Any]]:
        return self.list_jobs("active")

    def _queue_positions(self, conn: sqlite3.Connection) -> dict[str, int]:
        rows = conn.execute("SELECT id, options_json FROM jobs WHERE stage = 'queued'").fetchall()
        ordered = sorted(rows, key=_queue_key)
        return {r["id"]: i + 1 for i, r in enumerate(ordered)}

    def _to_dict(self, row: sqlite3.Row, positions: dict[str, int]) -> dict[str, Any]:
        stage = row["stage"]
        if stage in RUNNING_STAGES:
            qpos: int | None = 0
        elif stage == "queued":
            qpos = positions.get(row["id"])
        else:
            qpos = None
        sid = row["song_id"]
        thumb = f"/media/songs/{sid}/thumb.jpg" if row["song_status"] == "ready" else f"/api/thumbs/{sid}"
        return {
            "id": row["id"],
            "kind": row["kind"],
            "song_id": sid,
            "song": {"title": row["song_title"], "artist": row["song_artist"], "thumbnail": thumb},
            "stage": stage,
            "stage_progress": row["stage_progress"],
            "progress": round(float(row["progress"] or 0), 4),
            "queue_position": qpos,
            "device": row["device"],
            "lyrics_source": row["lyrics_source"],
            "error": (
                {
                    "code": row["error_code"],
                    "message": row["error_message"] or MESSAGES.get(row["error_code"], ""),
                }
                if row["error_code"]
                else None
            ),
            "attempts": row["attempts"],
            "requested_by": row["requested_by"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def active_job_for(
        self, conn: sqlite3.Connection, song_id: str, kind: str | None = None
    ) -> sqlite3.Row | None:
        sql = f"SELECT * FROM jobs WHERE song_id = ? AND stage IN ({_in(ACTIVE_STAGES)})"  # noqa: S608
        args: list[Any] = [song_id]
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        return conn.execute(sql + " ORDER BY created_at DESC LIMIT 1", args).fetchone()

    # ==== criação / controle ============================================================================
    def insert_job(
        self,
        conn: sqlite3.Connection,
        kind: str,
        song_id: str,
        actor: str,
        options: dict[str, Any] | None = None,
    ) -> str:
        job_id = f"j_{next_id(conn, 'job')}"
        options = {**(options or {}), "queue_seq": next_id(conn, "job_queue_seq")}
        now = utcnow()
        conn.execute(
            "INSERT INTO jobs(id, kind, song_id, stage, progress, options_json, attempts, requested_by, "
            "created_at, updated_at) VALUES (?, ?, ?, 'queued', 0, ?, 0, ?, ?, ?)",
            (job_id, kind, song_id, json.dumps(options), actor, now, now),
        )
        return job_id

    def notify_created(self, job_id: str) -> None:
        self.publish_job(job_id)
        self._wake.set()

    def cancel(self, job_id: str) -> None:
        """Só sinaliza, sem banco e sem bloquear: pede o fim do subprocesso (SIGTERM; o SIGKILL, se preciso,
        vem de uma thread à parte) e interrompe o download no próximo hook. Pode ser chamado dentro de uma
        transação: nunca espera o processo morrer.

        Só o job em execução é registrado em `_canceled_ids`. Um job ainda `queued` é barrado em `_run` pelo
        banco (linha apagada ou marcada `canceled`), então não precisa (nem deve, para não vazar) entrar no set.
        """
        with self._lock:
            ctx = self._current
            if ctx is None or ctx.job_id != job_id:
                return
            self._canceled_ids.add(job_id)
        ctx.cancel.set()
        self._kill(ctx, wait=False)

    def cancel_or_remove(self, job_id: str) -> bool:
        """DELETE /api/jobs/{id}: ativo → canceled; terminal → removido. False se não existe."""
        with self.db.tx() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return False
            if row["stage"] in TERMINAL_STAGES:
                conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
                removed = True
            else:
                removed = False
                if row["stage"] == "queued":
                    self._mark_canceled(conn, row)
        if removed:
            self.bus.publish("job.removed", {"id": job_id})
            return True
        if row["stage"] == "queued":
            self._after_terminal(job_id, row["song_id"], row["kind"])
        else:
            self.cancel(job_id)  # a thread do runner grava `canceled` quando o processo morrer
        return True

    def retry(self, job_id: str) -> dict[str, Any] | None:
        """error/canceled → queued. Retorna None se o job não existe; levanta ValueError se não é retentável."""
        with self.db.tx() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            if row["stage"] not in ("error", "canceled"):
                raise ValueError("job_not_retryable")
            now = utcnow()
            options = json.loads(row["options_json"] or "{}")
            options["queue_seq"] = next_id(conn, "job_queue_seq")
            conn.execute(
                "UPDATE jobs SET stage='queued', stage_progress=NULL, progress=0, attempts=0, error_code=NULL, "
                "error_message=NULL, lyrics_source=NULL, finished_at=NULL, options_json=?, updated_at=? WHERE id=?",
                (json.dumps(options), now, job_id),
            )
            if row["kind"] == "process":
                conn.execute(
                    "UPDATE songs SET status='queued', error_code=NULL, updated_at=? WHERE id=? AND status != 'ready'",
                    (now, row["song_id"]),
                )
        with self._lock:
            self._canceled_ids.discard(job_id)
        self.publish_job(job_id)
        self.publish_song(row["song_id"], "updated")
        self._wake.set()
        return self.job_dict(job_id)

    # ==== publicação ============================================================================
    def publish_job(self, job_id: str) -> None:
        job = self.job_dict(job_id)
        if job is not None:
            self.bus.publish("job.updated", job)

    def _publish_queue_positions(self) -> None:
        conn = self.db.connect()
        try:
            pos = self._queue_positions(conn)
        finally:
            conn.close()
        for jid in pos:
            self.publish_job(jid)

    def _song(self, song_id: str) -> dict[str, Any] | None:
        conn = self.db.connect()
        try:
            return song_payload(conn, song_id, "host")
        finally:
            conn.close()

    def publish_song(self, song_id: str, action: str) -> None:
        song = self._song(song_id)
        if song is not None:
            self.bus.publish("library.updated", {"action": action, "song_id": song_id, "song": song})

    def toast(self, level: str, message: str) -> None:
        self.bus.publish("toast", {"level": level, "message": message})

    # ==== laço principal ===========================================================================
    def _next_queued(self) -> sqlite3.Row | None:
        conn = self.db.connect()
        try:
            rows = conn.execute("SELECT * FROM jobs WHERE stage = 'queued'").fetchall()
        finally:
            conn.close()
        if not rows:
            return None
        return sorted(rows, key=_queue_key)[0]

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=2.0)
            self._wake.clear()
            while not self._stop.is_set():
                row = self._next_queued()
                if row is None:
                    break
                try:
                    self._run(row)
                except Exception:  # noqa: BLE001 — o laço nunca morre
                    log.exception("falha inesperada no runner (job %s)", row["id"])

    def run_pending(self) -> None:
        """Executa todos os jobs na fila na thread atual (testes)."""
        while (row := self._next_queued()) is not None:
            self._run(row)

    def _run(self, row: sqlite3.Row) -> None:
        job_id, kind, song_id = row["id"], row["kind"], row["song_id"]
        ctx = _Ctx(job_id, kind, song_id)
        with self._lock:
            if job_id in self._canceled_ids:
                ctx.cancel.set()
            self._current = ctx
        options = json.loads(row["options_json"] or "{}")
        try:
            with self.db.tx() as conn:
                # Só começa se o job ainda está `queued`: um DELETE/cancelamento entre `_next_queued` e aqui
                # apaga a linha ou marca `canceled`, e o job não pode rodar por cima disso.
                cur = conn.execute(
                    "UPDATE jobs SET attempts = attempts + 1, updated_at=? WHERE id=? AND stage='queued'",
                    (utcnow(), job_id),
                )
                if cur.rowcount == 0:
                    raise _NotQueued
                if kind == "process":
                    conn.execute(
                        "UPDATE songs SET status='processing', updated_at=? WHERE id=? AND status != 'ready'",
                        (utcnow(), song_id),
                    )
            self._publish_queue_positions()
            if kind == "process":
                self.publish_song(song_id, "updated")
                self._run_process(ctx, options)
            elif kind == "align":
                self._run_align(ctx, options)
            elif kind == "analyze":
                self._run_analyze(ctx, options)
            else:
                self._run_transcribe(ctx, options)
        except _NotQueued:
            log.info("job %s não está mais na fila; ignorado", job_id)
        except JobCanceled:
            if self._stop.is_set() and not self._is_user_canceled(job_id):
                return  # desligando: fica para a retomada
            self._finish_canceled(ctx)
        except JobError as exc:
            if self._stop.is_set() and not self._is_user_canceled(job_id):
                return
            log.warning("job %s falhou: %s (%s)", job_id, exc.code, exc.message)
            self._finish_error(ctx, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001
            if self._stop.is_set():
                return
            log.exception("job %s: erro inesperado", job_id)
            code = "disk_full" if isinstance(exc, OSError) and exc.errno == errno.ENOSPC else "internal_error"
            self._finish_error(ctx, code, MESSAGES[code])
        finally:
            with self._lock:
                self._current = None
                self._canceled_ids.discard(job_id)
            if not self._stop.is_set() or self._job_terminal(job_id):
                shutil.rmtree(self.config.tmp_dir / job_id, ignore_errors=True)

    def _is_user_canceled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._canceled_ids

    def _job_terminal(self, job_id: str) -> bool:
        conn = self.db.connect()
        try:
            r = conn.execute("SELECT stage FROM jobs WHERE id=?", (job_id,)).fetchone()
            return r is None or r["stage"] in TERMINAL_STAGES
        finally:
            conn.close()

    # ==== atualização de estado =========================================================================
    def _set_stage(self, ctx: _Ctx, stage: str, stage_progress: float | None = 0.0, **extra: Any) -> None:
        ctx.check()
        ctx.stage = stage
        lo, _ = ctx.ranges.get(stage, (0.0, 0.0))
        self._write(ctx, stage=stage, stage_progress=stage_progress, progress=lo, force=True, **extra)

    def _progress(self, ctx: _Ctx, frac: float | None, stage: str | None = None) -> None:
        ctx.check()
        lo, hi = ctx.ranges.get(stage or ctx.stage, (0.0, 0.0))
        prog = lo + (hi - lo) * (frac if frac is not None else 0.0)
        self._write(ctx, stage_progress=None if frac is None else round(frac, 3), progress=prog)

    def _write(self, ctx: _Ctx, force: bool = False, **fields: Any) -> None:
        now = time.monotonic()
        if not force and now - ctx.last_publish < PUBLISH_INTERVAL_S:
            return
        ctx.last_publish = now
        fields["updated_at"] = utcnow()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with contextlib.suppress(sqlite3.OperationalError), self.db.tx() as conn:
            conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), ctx.job_id))  # noqa: S608
        self.publish_job(ctx.job_id)

    def _mark_canceled(self, conn: sqlite3.Connection, row: sqlite3.Row) -> None:
        now = utcnow()
        conn.execute(
            "UPDATE jobs SET stage='canceled', stage_progress=NULL, error_code=NULL, error_message=NULL, "
            "updated_at=?, finished_at=? WHERE id=?",
            (now, now, row["id"]),
        )
        if row["kind"] == "process":
            conn.execute(
                "UPDATE songs SET status='error', error_code='canceled', updated_at=? WHERE id=? AND status != 'ready'",
                (now, row["song_id"]),
            )

    def _finish_canceled(self, ctx: _Ctx) -> None:
        with self.db.tx() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (ctx.job_id,)).fetchone()
            if row is None:  # música excluída (cascata)
                return
            self._mark_canceled(conn, row)
        self._after_terminal(ctx.job_id, ctx.song_id, ctx.kind)

    def _finish_error(self, ctx: _Ctx, code: str, message: str) -> None:
        now = utcnow()
        with self.db.tx() as conn:
            if conn.execute("SELECT 1 FROM jobs WHERE id=?", (ctx.job_id,)).fetchone() is None:
                return
            conn.execute(
                "UPDATE jobs SET stage='error', stage_progress=NULL, error_code=?, error_message=?, "
                "updated_at=?, finished_at=? WHERE id=?",
                (code, message, now, now, ctx.job_id),
            )
            if ctx.kind == "process":
                conn.execute(
                    "UPDATE songs SET status='error', error_code=?, updated_at=? WHERE id=? AND status != 'ready'",
                    (code, now, ctx.song_id),
                )
        self._after_terminal(ctx.job_id, ctx.song_id, ctx.kind)

    def _after_terminal(self, job_id: str, song_id: str, kind: str) -> None:
        self.publish_job(job_id)
        if kind == "process":
            self.publish_song(song_id, "updated")
        self._publish_queue_positions()

    def lrclib_client(self) -> lrclib.LrclibClient:
        factory = self._lrclib_factory or (
            getattr(self.app.state, "lrclib_factory", None) if self.app is not None else None
        )
        return (factory or lrclib.LrclibClient)()

    # ==== dispositivo / modelos ===========================================================================
    def devices(self) -> tuple[str, str]:
        """(device da separação, device do whisper)."""
        if self._device is None:
            if self.config.device == "cpu":
                self._device = ("cpu", "cpu")
            else:
                gpu = system.probe_gpu()
                dev, self._gpu_warning = system.resolve_device(self.config, gpu)
                whisper = "cuda" if dev == "cuda" and gpu.get("ct2_cuda_devices", 0) > 0 else "cpu"
                self._device = (dev, whisper)
        return self._device

    def settings(self) -> dict[str, Any]:
        try:
            from karaoke.api.settings import get_settings

            return get_settings(self.db, self.config)
        except Exception:  # noqa: BLE001
            return {"separation_model": self.config.separation_model, "whisper_model": None}

    def whisper_model(self, whisper_device: str) -> str:
        if self.config.whisper_model:
            return self.config.whisper_model
        if whisper_device != "cuda":
            explicit = self._explicit_setting("whisper_model")
            return explicit or "small"
        return self.settings().get("whisper_model") or "large-v3-turbo"

    def _explicit_setting(self, key: str) -> Any:
        conn = self.db.connect()
        try:
            r = conn.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(r["value_json"]) if r else None
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def _ensure_model(self, ctx: _Ctx, model_id: str) -> None:
        """Baixa o modelo se preciso, mostrando a etapa `model_download`. No meio do `lyrics` o progresso global
        continua na faixa do `lyrics` e, no fim, a etapa volta a ser a anterior."""
        if models.is_downloaded(self.config, model_id):
            return
        prev_stage = ctx.stage
        initial = prev_stage in ("queued", "model_download")
        if initial:
            self._set_stage(ctx, "model_download", 0.0)
        else:
            ctx.check()
            self._write(ctx, force=True, stage="model_download", stage_progress=0.0)

        def on_progress(frac: float) -> None:
            if initial:
                self._progress(ctx, frac)
            else:
                ctx.check()
                lo, hi = ctx.ranges.get(prev_stage, (0.0, 0.0))
                self._write(ctx, stage_progress=round(frac, 3), progress=lo + (hi - lo) * 0.2 * frac)
            self.bus_system_update()

        try:
            models.download(self.config, model_id, on_progress=on_progress, cancel=ctx.cancel)
        except models.ModelDownloadError as exc:
            ctx.check()
            cause = exc.__cause__
            if isinstance(cause, OSError) and cause.errno == errno.ENOSPC:
                raise JobError("disk_full") from exc
            raise JobError("model_download_failed", str(exc)) from exc
        finally:
            self.bus_system_update(force=True)
        ctx.check()
        if not initial:
            self._write(ctx, force=True, stage=prev_stage, stage_progress=None)

    def bus_system_update(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_sys < PUBLISH_INTERVAL_S:
            return
        self._last_sys = now
        dev = self._device[0] if self._device else None
        self.bus.publish(
            "system.updated",
            {"device": dev, "gpu_warning": self._gpu_warning, "model_download": models.current_download()},
        )

    # ==== subprocessos ==============================================================================
    def _kill(self, ctx: _Ctx, wait: bool = True) -> None:
        """SIGTERM e, se o processo não sair em 5 s, SIGKILL. Com `wait=False` a espera roda numa thread
        daemon e a chamada retorna na hora (usado no cancelamento, que pode estar segurando locks)."""
        proc = ctx.proc
        if proc is None or proc.poll() is not None:
            return
        with contextlib.suppress(OSError):
            proc.terminate()
        if wait:
            _reap(proc)
        else:
            threading.Thread(target=_reap, args=(proc,), name="job-kill", daemon=True).start()

    def _worker_env(self) -> dict[str, str]:
        env = system.cuda_env()
        env.update(system.model_env(self.config))
        env = system.venv_bin_on_path(env)
        env["HF_HUB_OFFLINE"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        env.setdefault("TOKENIZERS_PARALLELISM", "false")
        return env

    def _run_worker(
        self,
        ctx: _Ctx,
        task: str,
        params: dict[str, Any],
        on_progress: Callable[[float], None],
        workdir: Path,
        result_file: Path | None = None,
    ) -> dict[str, Any]:
        ctx.check()
        workdir.mkdir(parents=True, exist_ok=True)
        task_file = workdir / f"task-{task}.json"
        task_file.write_text(
            json.dumps(
                {"task": task, "params": params, "result_file": str(result_file) if result_file else None}
            )
        )
        log_path = workdir / f"worker-{task}.log"
        result: dict[str, Any] | None = None
        error: tuple[str, str] | None = None
        with open(log_path, "w", encoding="utf-8") as logf:
            proc = subprocess.Popen(
                [sys.executable, "-m", "karaoke.jobs.worker", str(task_file)],
                stdout=subprocess.PIPE,
                stderr=logf,
                stdin=subprocess.DEVNULL,
                env=self._worker_env(),
                cwd=str(PROJECT_DIR),
                text=True,
                encoding="utf-8",
            )
            ctx.proc = proc
            if ctx.cancel.is_set():
                self._kill(ctx)
            assert proc.stdout is not None
            for line in proc.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                t = msg.get("type")
                if t == "progress":
                    with contextlib.suppress(JobCanceled):
                        on_progress(float(msg.get("value") or 0))
                elif t == "result":
                    result = msg.get("data") or {}
                elif t == "error":
                    error = (msg.get("code") or "internal_error", msg.get("message") or "")
                    log.warning("worker %s: %s", task, msg.get("detail"))
                elif t == "log":
                    log.info("worker %s: %s", task, msg.get("message"))
            proc.wait()
            ctx.proc = None
        ctx.check()
        if error:
            raise JobError(error[0], error[1] or None)
        if result is None:
            tail = _tail(log_path)
            log.error("worker %s terminou sem resultado (código %s): %s", task, proc.returncode, tail)
            if "out of memory" in tail.lower():
                raise JobError("gpu_out_of_memory")
            raise JobError("separation_failed" if task == "separate" else "transcription_failed")
        if result_file is not None:
            return json.loads(Path(result["result_file"]).read_text(encoding="utf-8"))
        return result

    def _ffmpeg(self, ctx: _Ctx, args: list[str], code: str = "ffmpeg_failed") -> None:
        ctx.check()
        ff = shutil.which("ffmpeg")
        if not ff:
            raise JobError("ffmpeg_failed", "ffmpeg não encontrado. Instale o ffmpeg.")
        proc = subprocess.Popen(
            [ff, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        ctx.proc = proc  # type: ignore[assignment]
        _, err = proc.communicate()
        ctx.proc = None
        ctx.check()
        if proc.returncode != 0:
            log.error("ffmpeg falhou: %s", (err or "")[-800:])
            if "No space left" in (err or ""):
                raise JobError("disk_full")
            raise JobError(code)

    def _ffmpeg_parallel(self, ctx: _Ctx, jobs: list[list[str]]) -> None:
        ff = shutil.which("ffmpeg")
        if not ff:
            raise JobError("ffmpeg_failed", "ffmpeg não encontrado. Instale o ffmpeg.")
        procs = [
            subprocess.Popen(
                [ff, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *a],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            for a in jobs
        ]
        errs = []
        for p in procs:
            while True:
                try:
                    _, err = p.communicate(timeout=0.5)
                    errs.append((p.returncode, err or ""))
                    break
                except subprocess.TimeoutExpired:
                    if ctx.cancel.is_set():
                        for q in procs:
                            with contextlib.suppress(OSError):
                                q.kill()
        ctx.check()
        for rc, err in errs:
            if rc != 0:
                log.error("ffmpeg falhou: %s", err[-800:])
                raise JobError("disk_full" if "No space left" in err else "ffmpeg_failed")

    # ==== pipeline: process =============================================================================
    def lyrics_mode(self, options: dict[str, Any]) -> str:
        """Override do job (se válido) → setting `lyrics_mode` (se válido) → `aligned`. `transcribe` é alias legado de
        `aligned` (CR-001: o texto vem sempre da letra oficial; o Whisper só dá os tempos)."""
        for mode in (options.get("lyrics_mode"), self.settings().get("lyrics_mode")):
            if mode in LYRICS_MODES:
                return LEGACY_MODES.get(mode, mode)
        return DEFAULT_LYRICS_MODE

    def _run_process(self, ctx: _Ctx, options: dict[str, Any] | None = None) -> None:
        mode = self.lyrics_mode(options or {})
        cfg = self.config
        work = cfg.tmp_dir / ctx.job_id
        work.mkdir(parents=True, exist_ok=True)
        song = self._song_row(ctx.song_id)
        if song is None:
            raise JobCanceled()
        sep_device, whisper_device = self.devices()
        sep_model = self.settings().get("separation_model") or cfg.separation_model
        if sep_model not in models.SEPARATION_MODELS:
            sep_model = "htdemucs_ft"
        self._write(ctx, force=True, device=sep_device)
        t0 = time.monotonic()
        timings: dict[str, float] = {}

        # 1. modelo de separação
        if not models.is_downloaded(cfg, sep_model):
            ctx.ranges = PROCESS_RANGES
            self._ensure_model(ctx, sep_model)
        else:
            ctx.ranges = _redistribute(PROCESS_RANGES)
        timings["model_download"] = time.monotonic() - t0

        # 2. download (pulado na retomada se source.wav já existe)
        t = time.monotonic()
        self._set_stage(ctx, "downloading", 0.0)
        wav = work / "source.wav"
        if not wav.is_file():
            try:
                # v0.3: job de convidado leva `max_duration_ms` (defesa em profundidade → `video_too_long`)
                max_ms = (options or {}).get("max_duration_ms")
                limit = {"max_duration_ms": int(max_ms)} if max_ms else {}
                src = yt.download_audio(
                    ctx.song_id,
                    work / "dl",
                    progress=lambda f: self._progress(ctx, None if f is None else f * 0.85),
                    cancel=ctx.cancel,
                    **limit,
                )
            except yt.DownloadCanceled as exc:
                raise JobCanceled() from exc
            except yt.YoutubeError as exc:
                raise JobError(exc.code, exc.message) from exc
            self._progress(ctx, 0.9)
            tmp_wav = work / "source.tmp.wav"
            self._ffmpeg(
                ctx, ["-i", str(src), "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_f32le", str(tmp_wav)]
            )
            os.replace(tmp_wav, wav)
            shutil.rmtree(work / "dl", ignore_errors=True)
        if not (work / "thumb.jpg").is_file():
            yt.fetch_thumbnail(ctx.song_id, work / "thumb.jpg", large=True)
        self._progress(ctx, 1.0)
        timings["downloading"] = time.monotonic() - t

        # 3. separação (subprocesso)
        t = time.monotonic()
        self._set_stage(ctx, "separating", 0.0)
        stems = work / "stems"
        sep = self._run_worker(
            ctx,
            "separate",
            {"input": str(wav), "out_dir": str(stems), "model": sep_model, "device": sep_device},
            lambda f: self._progress(ctx, f),
            work,
        )
        if sep.get("device") and sep["device"] != sep_device:
            self._write(ctx, force=True, device=sep["device"])
        duration_ms = (
            round(sep["samples"] * 1000 / sep["samplerate"]) if sep.get("samples") else song["duration_ms"]
        )
        timings["separating"] = time.monotonic() - t

        # 4. letra
        t = time.monotonic()
        self._set_stage(ctx, "lyrics", None)
        cands, selected_idx, source, transcript = self._find_lyrics(
            ctx, song, duration_ms, stems / "vocals.wav", whisper_device, work, mode
        )
        timings["lyrics"] = time.monotonic() - t

        # 5. finalização: opus + move atômico + transação
        t = time.monotonic()
        self._set_stage(ctx, "finalizing", 0.0)
        out = work / "out"
        out.mkdir(exist_ok=True)
        enc = ["-c:a", "libopus", "-b:a", "160k", "-vbr", "on", "-ar", "48000", "-application", "audio"]
        self._ffmpeg_parallel(
            ctx,
            [
                ["-i", str(stems / "instrumental.wav"), *enc, str(out / "instrumental.opus")],
                ["-i", str(stems / "vocals.wav"), *enc, str(out / "vocals.opus")],
            ],
        )
        if (work / "thumb.jpg").is_file():
            shutil.copyfile(work / "thumb.jpg", out / "thumb.jpg")
        if transcript is not None:
            save_transcript(out / TRANSCRIPT_FILE, transcript)
        self._progress(ctx, 0.6)
        size = sum(f.stat().st_size for f in out.iterdir() if f.is_file() and f.suffix != ".json")
        ctx.check()
        self._commit_song(ctx, out, cands, selected_idx, source, duration_ms, size)
        timings["finalizing"] = time.monotonic() - t
        timings["total"] = time.monotonic() - t0
        log.info(
            "job %s pronto (%s): %s",
            ctx.job_id,
            ctx.song_id,
            ", ".join(f"{k}={v:.1f}s" for k, v in timings.items()),
        )
        with contextlib.suppress(OSError):
            (cfg.logs_dir / "timings.jsonl").open("a").write(
                json.dumps(
                    {
                        "job": ctx.job_id,
                        "song": ctx.song_id,
                        "device": sep_device,
                        "model": sep_model,
                        "lyrics": source,
                        "duration_ms": duration_ms,
                        **{k: round(v, 2) for k, v in timings.items()},
                    }
                )
                + "\n"
            )

    def _song_row(self, song_id: str) -> sqlite3.Row | None:
        conn = self.db.connect()
        try:
            return conn.execute("SELECT * FROM songs WHERE id=?", (song_id,)).fetchone()
        finally:
            conn.close()

    def _find_lyrics(
        self,
        ctx: _Ctx,
        song: sqlite3.Row,
        duration_ms: int | None,
        vocals: Path,
        whisper_device: str,
        work: Path,
        mode: str = DEFAULT_LYRICS_MODE,
    ) -> tuple[list[dict[str, Any]], int | None, str | None, dict[str, Any] | None]:
        """Retorna (candidatas, índice da selecionada, fonte, transcrição crua|None).

        Modos (setting `lyrics_mode`, ou override do job; `transcribe` = alias de `aligned`): `online` = LRCLIB com
        tempos como está; sem ela, igual a `aligned`. `aligned` = texto da letra oficial (LRCLIB com tempos ou só
        texto) + tempos da voz; o texto do Whisper só é usado se nenhuma letra oficial casar com a voz (CR-001)."""
        cands, top_synced = self._official_candidates(ctx, song, duration_ms)
        self._progress(ctx, 0.2)
        if mode == "online" and top_synced is not None:
            self.toast("info", "Letra encontrada no LRCLIB")
            return cands, top_synced, "lrclib", None
        # Whisper sobre a voz separada: tempos para a letra oficial (ou o texto, se não houver letra oficial)
        bases = text_base_order(cands, duration_ms, song["title"])
        self._write(ctx, force=True, lyrics_source="aligned" if bases else "whisper", stage_progress=None)
        res = self._whisper(ctx, vocals, whisper_device, None, work)
        al_idx = try_align(cands, bases, res, duration_ms)
        if al_idx is not None:
            self.toast("info", "Letra oficial sincronizada pela voz")
            return cands, al_idx, "aligned", res
        if top_synced is not None:
            # a voz não confirmou a letra (Whisper fraco/voz difícil), mas o texto oficial vale mais que o do Whisper
            log.info("alinhamento fraco: mantendo a letra do LRCLIB com os tempos dela")
            return cands, top_synced, "lrclib", res
        if bases:
            log.info(
                "letra oficial não casou com a voz (%d tentativas): usando o texto do Whisper", len(bases)
            )
        wc = whisper_candidate(res, duration_ms)
        if wc["lines"]:
            cands.append(wc)
            self.toast("warning", "Letra oficial não encontrada — letra extraída da voz (pode ter erros).")
            return cands, len(cands) - 1, "whisper", res
        # sem fala detectada (instrumental): música fica pronta sem letra
        return cands, None, None, res

    def _official_candidates(
        self, ctx: _Ctx, song: sqlite3.Row, duration_ms: int | None, known: set[Any] | None = None
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Busca no LRCLIB (cadeia de `lrclib.find_candidates`) → candidatas `lrclib`/`online_plain` (sem as de
        `provider_id` em `known`) e o índice da melhor com tempos e duração aceitável. Sem rede: ([], None)."""
        track = song["title"]
        artist = song["artist"] or clean_channel(song["channel"])
        records: list[lrclib.LrcRecord] = []
        client = self.lrclib_client()
        try:
            records = lrclib.find_candidates(client, track, artist, duration_ms, song["original_title"])
        except lrclib.LrclibNetworkError as exc:
            log.info("LRCLIB indisponível (%s): sem letra oficial", exc)
        finally:
            with contextlib.suppress(Exception):
                client.close()
        ctx.check()
        cands: list[dict[str, Any]] = []
        top: int | None = None
        for rec in records:
            if known and rec.id in known:
                continue
            c = lrclib_candidate(rec, duration_ms)
            if c is None:
                continue
            if top is None and c["source"] == "lrclib" and lrclib.acceptable(rec, duration_ms):
                top = len(cands)
            cands.append(c)
        return cands, top

    def _whisper(
        self, ctx: _Ctx, audio: Path, device: str, language: str | None, work: Path
    ) -> dict[str, Any]:
        """Transcrição crua do worker (segmentos em segundos + envelope do vocal) com o modelo usado."""
        model = self.whisper_model(device)
        self._ensure_model(ctx, model)
        if ctx.stage != "lyrics":
            self._set_stage(ctx, "lyrics", 0.0)
        base = 0.2 if ctx.kind == "process" else 0.0
        res = self._run_worker(
            ctx,
            "transcribe",
            {"input": str(audio), "model": model, "device": device, "language": language},
            lambda f: self._progress(ctx, base + (1 - base) * f * 0.98),
            work,
            result_file=work / "transcript.json",
        )
        res = dict(res)
        res["model"] = model
        res["language_requested"] = language
        res["created_at"] = utcnow()
        return res

    def _transcribe(
        self,
        ctx: _Ctx,
        audio: Path,
        device: str,
        language: str | None,
        work: Path,
        duration_ms: int | None,
    ) -> dict[str, Any] | None:
        return whisper_candidate(self._whisper(ctx, audio, device, language, work), duration_ms)

    def _song_transcript(
        self, ctx: _Ctx, song: sqlite3.Row, language: str | None, work: Path, reuse: bool = True
    ) -> dict[str, Any]:
        """Transcrição da faixa de voz de uma música pronta: do cache (data/songs/<id>/transcript.json) ou nova
        (e aí grava o cache)."""
        song_dir = self.config.songs_dir / ctx.song_id
        if reuse:
            cached = load_transcript(self.config, ctx.song_id)
            if cached is not None and (not language or cached.get("language") == language):
                ctx.ranges = {"lyrics": (0.0, 1.0)}
                self._set_stage(ctx, "lyrics", 0.95)
                return cached
        vocals = song_dir / "vocals.opus"
        if song["status"] != "ready" or not vocals.is_file():
            raise JobError("transcription_failed", "A música não está pronta.")
        _, whisper_device = self.devices()
        self._write(ctx, force=True, device=whisper_device)
        model = self.whisper_model(whisper_device)
        ctx.ranges = (
            TRANSCRIBE_RANGES if not models.is_downloaded(self.config, model) else {"lyrics": (0.0, 1.0)}
        )
        res = self._whisper(ctx, vocals, whisper_device, language, work)
        ctx.check()
        with contextlib.suppress(OSError):
            save_transcript(song_dir / TRANSCRIPT_FILE, res)
        return res

    def _commit_song(
        self,
        ctx: _Ctx,
        out: Path,
        cands: list[dict[str, Any]],
        selected_idx: int | None,
        source: str | None,
        duration_ms: int | None,
        size: int,
    ) -> None:
        songs_dir = self.config.songs_dir
        songs_dir.mkdir(parents=True, exist_ok=True)
        final = songs_dir / ctx.song_id
        old = songs_dir / f".old-{ctx.song_id}-{ctx.job_id}"
        moved_old = False
        if final.exists():
            os.replace(final, old)
            moved_old = True
        os.replace(out, final)  # mesmo sistema de arquivos (data/tmp → data/songs): atômico
        try:
            with self.db.tx() as conn:
                if conn.execute("SELECT 1 FROM songs WHERE id=?", (ctx.song_id,)).fetchone() is None:
                    raise JobCanceled()  # música excluída durante o processamento
                ids = insert_candidates(conn, ctx.song_id, cands, duration_ms)
                selected = ids[selected_idx] if selected_idx is not None else None
                now = utcnow()
                conn.execute(
                    "UPDATE songs SET status='ready', error_code=NULL, duration_ms=COALESCE(?, duration_ms), "
                    "size_bytes=?, media_version=media_version+1, lyrics_candidate_id=?, updated_at=? WHERE id=?",
                    (duration_ms, size, selected, now, ctx.song_id),
                )
                conn.execute(
                    "UPDATE jobs SET stage='done', stage_progress=1, progress=1, lyrics_source=?, error_code=NULL, "
                    "error_message=NULL, updated_at=?, finished_at=? WHERE id=?",
                    (source, now, now, ctx.job_id),
                )
        except BaseException:
            shutil.rmtree(final, ignore_errors=True)
            if moved_old:
                os.replace(old, final)
            raise
        if moved_old:
            shutil.rmtree(old, ignore_errors=True)
        self.publish_job(ctx.job_id)
        self.publish_song(ctx.song_id, "added")
        if source is None:
            self.toast("warning", "Letra não encontrada — a música vai tocar sem letra.")
        queue = getattr(self.app.state, "queue", None) if self.app is not None else None
        if queue is not None and hasattr(queue, "song_changed"):
            try:
                queue.song_changed(ctx.song_id)
            except Exception:  # noqa: BLE001
                log.exception("queue.song_changed falhou")

    # ==== pipeline: transcribe ===========================================================================
    def _run_transcribe(self, ctx: _Ctx, options: dict[str, Any]) -> None:
        work = self.config.tmp_dir / ctx.job_id
        work.mkdir(parents=True, exist_ok=True)
        song = self._song_row(ctx.song_id)
        if song is None:
            raise JobCanceled()
        self._write(ctx, force=True, lyrics_source="whisper")
        res = self._song_transcript(ctx, song, options.get("language"), work, reuse=False)
        cand = whisper_candidate(res, song["duration_ms"])
        if not cand["lines"]:
            raise JobError("transcription_failed", "Nenhuma letra foi reconhecida na faixa de voz.")
        self._commit_lyrics_job(ctx, cand, song["duration_ms"], "whisper")

    def _run_align(self, ctx: _Ctx, options: dict[str, Any]) -> None:
        """Sincronizar pela voz: texto da candidata `options.candidate_id` + tempos do vocal → nova `aligned`.
        Sem `candidate_id` (`search: true`): busca a letra oficial no LRCLIB antes; se nada servir, a nova
        candidata é o texto do Whisper ("Extraída da voz (sem letra oficial)", CR-001)."""
        work = self.config.tmp_dir / ctx.job_id
        work.mkdir(parents=True, exist_ok=True)
        song = self._song_row(ctx.song_id)
        if song is None:
            raise JobCanceled()
        cid = options.get("candidate_id")
        if cid:
            base = self._candidate_row(ctx.song_id, cid)
            if base is None:
                raise JobError(
                    "align_failed", "A letra de origem não existe mais. Escolha outra e tente de novo."
                )
            self._write(ctx, force=True, lyrics_source="aligned")
            res = self._song_transcript(ctx, song, options.get("language"), work)
            cand = aligned_candidate(candidate_from_row(base), res, song["duration_ms"])
            if cand is None or cand["meta"]["align"]["anchored_lines"] == 0:
                raise JobError(
                    "align_failed",
                    "Não encontrei a voz cantando esta letra. Confira se a letra é desta música ou cole a "
                    "letra certa.",
                )
            cand["derived_from"] = base["id"]
            self._commit_lyrics_job(ctx, cand, song["duration_ms"], "aligned")
            return
        # sem letra oficial guardada: busca primeiro
        self._set_stage(ctx, "lyrics", None)
        conn = self.db.connect()
        try:
            known = {
                json.loads(r["meta_json"] or "{}").get("provider_id")
                for r in conn.execute(
                    "SELECT meta_json FROM lyrics_candidates WHERE song_id=? AND source IN ('lrclib','online_plain')",
                    (ctx.song_id,),
                )
            }
        finally:
            conn.close()
        found, _ = self._official_candidates(ctx, song, song["duration_ms"], known)
        bases = text_base_order(found, song["duration_ms"], song["title"])
        self._write(ctx, force=True, lyrics_source="aligned" if bases else "whisper")
        res = self._song_transcript(ctx, song, options.get("language"), work)
        al_idx = try_align(found, bases, res, song["duration_ms"])
        if al_idx is not None:
            cand = found.pop(al_idx)
            self._commit_lyrics_job(ctx, cand, song["duration_ms"], "aligned", extra=found)
            return
        cand = whisper_candidate(res, song["duration_ms"])
        if not cand["lines"]:
            raise JobError("transcription_failed", "Não encontrei a letra oficial nem reconheci a voz.")
        self._commit_lyrics_job(ctx, cand, song["duration_ms"], "whisper", extra=found)

    def _run_analyze(self, ctx: _Ctx, options: dict[str, Any]) -> None:
        """Detectar offset sem transcrição em cache: transcreve (e grava o cache) e publica a sugestão."""
        work = self.config.tmp_dir / ctx.job_id
        work.mkdir(parents=True, exist_ok=True)
        song = self._song_row(ctx.song_id)
        if song is None:
            raise JobCanceled()
        res = self._song_transcript(ctx, song, None, work)
        base = self._candidate_row(ctx.song_id, options.get("candidate_id"))
        result = (
            detect_offset_for(base, res)
            if base is not None
            else {"suggested_offset_ms": 0, "confidence": 0.0, "anchored_lines": 0, "lines": 0}
        )
        now = utcnow()
        with self.db.tx() as conn:
            if conn.execute("SELECT 1 FROM jobs WHERE id=?", (ctx.job_id,)).fetchone() is None:
                raise JobCanceled()
            conn.execute(
                "UPDATE jobs SET stage='done', stage_progress=1, progress=1, updated_at=?, finished_at=? "
                "WHERE id=?",
                (now, now, ctx.job_id),
            )
        self.publish_job(ctx.job_id)
        self.bus.publish(
            "lyrics.offset_detected",
            {
                "song_id": ctx.song_id,
                "candidate_id": base["id"] if base is not None else None,
                "current_offset_ms": int(song["offset_ms"] or 0),
                **result,
            },
        )

    def _candidate_row(self, song_id: str, candidate_id: str | None) -> sqlite3.Row | None:
        if not candidate_id:
            return None
        conn = self.db.connect()
        try:
            return conn.execute(
                "SELECT * FROM lyrics_candidates WHERE id=? AND song_id=?", (candidate_id, song_id)
            ).fetchone()
        finally:
            conn.close()

    def _commit_lyrics_job(
        self,
        ctx: _Ctx,
        cand: dict[str, Any],
        duration_ms: int | None,
        source: str,
        extra: list[dict[str, Any]] | None = None,
    ) -> None:
        """Grava a candidata (e as `extra` encontradas na busca, antes dela — `derived_from_idx` da candidata
        aponta para elas), seleciona, fecha o job e publica lyrics.updated/song.updated/library.updated."""
        ctx.check()
        with self.db.tx() as conn:
            if conn.execute("SELECT 1 FROM songs WHERE id=?", (ctx.song_id,)).fetchone() is None:
                raise JobCanceled()
            cid = insert_candidates(conn, ctx.song_id, [*(extra or []), cand], duration_ms)[-1]
            now = utcnow()
            conn.execute(
                "UPDATE songs SET lyrics_candidate_id=?, updated_at=? WHERE id=?", (cid, now, ctx.song_id)
            )
            conn.execute(
                "UPDATE jobs SET stage='done', stage_progress=1, progress=1, lyrics_source=?, "
                "updated_at=?, finished_at=? WHERE id=?",
                (source, now, now, ctx.job_id),
            )
        self.publish_job(ctx.job_id)
        self.bus.publish("lyrics.updated", {"song_id": ctx.song_id, "candidate_id": cid, "source": source})
        song_d = self._song(ctx.song_id)
        if song_d is not None:
            self.bus.publish("song.updated", song_d)
            self.bus.publish("library.updated", {"action": "updated", "song_id": ctx.song_id, "song": song_d})


# ==== helpers de módulo ====================================================================================
_JOB_SELECT = """
SELECT j.*, s.title AS song_title, s.artist AS song_artist, s.status AS song_status
  FROM jobs j JOIN songs s ON s.id = j.song_id
"""


def _in(values: tuple[str, ...]) -> str:
    return ",".join(f"'{v}'" for v in values)


def _invert(s: str) -> str:
    return "".join(chr(0x10FFFF - ord(c)) for c in s)


def _redistribute(ranges: dict[str, tuple[float, float]]) -> dict[str, tuple[float, float]]:
    """Sem download de modelo: a faixa 0–10 % é redistribuída proporcionalmente entre as outras etapas."""
    lo0 = ranges["model_download"][1]
    span = 1.0 - lo0
    return {
        k: ((a - lo0) / span, (b - lo0) / span) for k, (a, b) in ranges.items() if k != "model_download"
    } | {"model_download": (0.0, 0.0)}


KILL_GRACE_S = 5.0  # espera após o SIGTERM antes do SIGKILL


def _reap(proc: subprocess.Popen, timeout: float | None = None) -> None:
    """Espera o processo sair após o SIGTERM; se não sair em `timeout` (padrão KILL_GRACE_S), SIGKILL."""
    timeout = KILL_GRACE_S if timeout is None else timeout
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=timeout)


def _tail(path: Path, n: int = 2000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-n:]
    except OSError:
        return ""


def whisper_candidate(res: dict[str, Any], duration_ms: int | None) -> dict[str, Any]:
    lines = builder.from_whisper(res.get("segments") or [], duration_ms)
    return {
        "source": "whisper",
        "timing": builder.timing_of(lines),
        "lines": lines,
        "language": res.get("language"),
        "meta": {
            "model": res.get("model"),
            "language": res.get("language"),
            "duration_ms": None,
            "provider_id": None,
        },
    }


TEXT_SOURCES = ("lrclib", "online_plain", "manual", "aligned")  # candidatas com texto oficial


def candidate_from_row(row: sqlite3.Row) -> dict[str, Any]:
    doc = json.loads(row["lyrics_json"])
    return {
        "source": row["source"],
        "lines": doc.get("lines") or [],
        "text_lines": doc.get("text_lines"),
        "meta": json.loads(row["meta_json"] or "{}"),
    }


def text_base_order(cands: list[dict[str, Any]], duration_ms: int | None, track: str) -> list[int]:
    """Índices das candidatas oficiais a tentar no alinhamento, em ordem: com tempos e duração aceitável, só
    texto com duração aceitável, depois as de mesmo nome de faixa (versão de outra duração — a cobertura do
    alinhamento decide). No máx. `MAX_TEXT_BASES`."""
    from karaoke.lyrics.titleclean import normalize

    def ok(c: dict[str, Any]) -> bool:
        d = c["meta"].get("duration_ms")
        return d is not None and bool(duration_ms) and abs(d - duration_ms) <= lrclib.ACCEPT_DIFF_MS

    nt = normalize(track)
    groups: list[list[int]] = [[], [], []]
    for i, c in enumerate(cands):
        if c["source"] not in ("lrclib", "online_plain"):
            continue
        if ok(c):
            groups[0 if c["source"] == "lrclib" else 1].append(i)
        elif normalize(c["meta"].get("track_name")) == nt:
            groups[2].append(i)
    return [i for g in groups for i in g][:MAX_TEXT_BASES]


def try_align(
    cands: list[dict[str, Any]], bases: list[int], res: dict[str, Any], duration_ms: int | None
) -> int | None:
    """Alinha cada base em ordem; a 1ª com cobertura ≥ MIN_ALIGN_COVERAGE vira uma candidata `aligned` acrescentada
    a `cands` (com `derived_from_idx`). Retorna o índice dela, ou None."""
    for i in bases:
        al = aligned_candidate(cands[i], res, duration_ms)
        if al is not None and al["meta"]["align"]["coverage"] >= MIN_ALIGN_COVERAGE:
            al["derived_from_idx"] = i
            cands.append(al)
            return len(cands) - 1
        log.info("alinhamento fraco com %s: %s", cands[i]["source"], al["meta"]["align"] if al else None)
    return None


def aligned_candidate(
    base: dict[str, Any], res: dict[str, Any], duration_ms: int | None
) -> dict[str, Any] | None:
    """Texto de `base` (lrclib/online_plain/manual, com ou sem tempos) + tempos da transcrição → `aligned`."""
    segs, env = res.get("segments") or [], res.get("envelope")
    if base.get("lines"):
        lines, stats = align.align_lines(base["lines"], segs, duration_ms, env)
    elif base.get("text_lines"):
        lines, stats = align.align_text(base["text_lines"], segs, duration_ms, env)
    else:
        return None
    if not lines:
        return None
    meta = base.get("meta") or {}
    return {
        "source": "aligned",
        "timing": builder.timing_of(lines),
        "lines": lines,
        "language": res.get("language"),
        "meta": {
            "track_name": meta.get("track_name"),
            "artist_name": meta.get("artist_name"),
            "album_name": meta.get("album_name"),
            "provider_id": meta.get("provider_id"),
            "duration_ms": meta.get("duration_ms"),
            "model": res.get("model"),
            "language": res.get("language"),
            "base_source": base.get("source"),
            "align": stats.as_meta(),
        },
    }


def detect_offset_for(base: sqlite3.Row, res: dict[str, Any]) -> dict[str, Any]:
    """Offset sugerido (só faz sentido para candidata com tempos; só texto → confiança 0)."""
    lines = json.loads(base["lyrics_json"]).get("lines") or []
    return align.detect_offset(lines, res.get("segments") or [], res.get("envelope"))


def transcript_path(config: Config, song_id: str) -> Path:
    return config.songs_dir / song_id / TRANSCRIPT_FILE


def load_transcript(config: Config, song_id: str) -> dict[str, Any] | None:
    """Transcrição em cache da música (ou None se não houver / estiver corrompida)."""
    p = transcript_path(config, song_id)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("segments"), list) else None


def save_transcript(path: Path, res: dict[str, Any]) -> None:
    """Grava o cache de forma atômica (tmp + rename)."""
    keep = ("segments", "envelope", "language", "language_probability", "duration_s", "model", "created_at")
    data = {"version": 1, **{k: res.get(k) for k in keep}}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def lrclib_candidate(rec: lrclib.LrcRecord, duration_ms: int | None) -> dict[str, Any] | None:
    """Registro LRCLIB → `lrclib` (com tempos) ou `online_plain` (só texto: `timing none`, `text_lines`)."""
    lines, _ = builder.from_lrc(rec.synced_lyrics or "", duration_ms)
    text_lines = None if lines else builder.text_lines_of(rec.plain_lyrics or "")
    if not lines and not text_lines:
        return None
    return {
        "source": "lrclib" if lines else "online_plain",
        "timing": builder.timing_of(lines),
        "lines": lines,
        "text_lines": text_lines,
        "language": None,
        "meta": {
            "track_name": rec.track_name,
            "artist_name": rec.artist_name,
            "album_name": rec.album_name,
            "provider_id": rec.id,
            "duration_ms": rec.duration_ms,
            "model": None,
        },
    }


def insert_candidates(
    conn: sqlite3.Connection, song_id: str, cands: list[dict[str, Any]], duration_ms: int | None
) -> list[str]:
    """Grava candidatas (documento completo em lyrics_json; resumo em meta_json). Retorna os ids."""
    ids: list[str] = []
    for c in cands:
        cid = f"{c['source']}:{next_id(conn, 'lyrics_' + c['source'])}"
        derived = c.get("derived_from")
        if derived is None and c.get("derived_from_idx") is not None:
            derived = ids[c["derived_from_idx"]]
        now = utcnow()
        meta = dict(c["meta"])
        meta["created_at"] = now
        cand_dur = meta.get("duration_ms")
        meta["duration_diff_ms"] = cand_dur - duration_ms if cand_dur is not None and duration_ms else None
        texts = [ln["text"] for ln in c["lines"]] or list(c.get("text_lines") or [])
        meta["line_count"] = len(texts)
        meta["preview"] = texts[:3]
        meta["language"] = c.get("language") or meta.get("language")
        meta["derived_from"] = derived
        doc = builder.make_document(
            song_id=song_id,
            candidate_id=cid,
            source=c["source"],
            lines=c["lines"],
            language=meta["language"],
            duration_ms=duration_ms,
            meta=meta,
            text_lines=None if c["lines"] else c.get("text_lines"),
        )
        conn.execute(
            "INSERT INTO lyrics_candidates(id, song_id, source, timing, meta_json, lyrics_json, derived_from, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cid,
                song_id,
                c["source"],
                doc["timing"],
                json.dumps(meta, ensure_ascii=False),
                json.dumps(doc, ensure_ascii=False),
                derived,
                now,
            ),
        )
        ids.append(cid)
    return ids


def song_payload(conn: sqlite3.Connection, song_id: str, actor: str) -> dict[str, Any] | None:
    """Objeto Song via `services.library` (T-005)."""
    from karaoke.services.library import load_song

    return load_song(conn, song_id, actor)


# ==== ciclo de vida (chamado por app.py) =======================================================================
def startup(app: FastAPI) -> None:
    runner = JobRunner(
        app.state.config,
        app.state.db,
        app.state.bus,
        app=app,
        autostart=getattr(app.state, "runner_autostart", True),
    )
    app.state.runner = runner
    # interfaces lidas pelo core (T-005): hello do SSE
    app.state.jobs_snapshot = lambda actor="host": runner.active_jobs()
    app.state.model_download = models.current_download()

    def mirror(value: dict[str, Any] | None) -> None:
        app.state.model_download = value

    runner._model_listener = mirror
    models.add_listener(mirror)
    runner.start()


def shutdown(app: FastAPI) -> None:
    runner: JobRunner | None = getattr(app.state, "runner", None)
    if runner is not None:
        runner.stop()
        listener = getattr(runner, "_model_listener", None)
        if listener is not None:
            models.remove_listener(listener)
