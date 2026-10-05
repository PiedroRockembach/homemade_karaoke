"""Apoio aos testes do backend core (T-005): sementes no banco, dois atores e servidor real para SSE."""

from __future__ import annotations

import contextlib
import json
import queue
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn

from karaoke import security
from karaoke.app import create_app
from karaoke.config import Config
from karaoke.security import HOST, Principal

GUEST_X = Principal(role="guest", id="guest:x", name="X")

# ids de vídeo válidos (11 chars)
SONG_A = "fJ9rUzIMcZQ"
SONG_B = "dQw4w9WgXcQ"
SONG_C = "kJQP7kiw5Fk"
SONG_D = "9bZkp7q19f0"

_seq = iter(range(1, 10**6))


def seed_song(
    app: Any,
    song_id: str = SONG_A,
    *,
    title: str = "Bohemian Rhapsody",
    artist: str | None = "Queen",
    status: str = "ready",
    added_by: str = "host",
    duration_ms: int = 200_000,
    created_at: str | None = None,
    files: bool = False,
) -> None:
    created = created_at or f"2026-10-04T00:{next(_seq) % 60:02d}:00Z"
    with app.state.db.tx() as conn:
        conn.execute(
            "INSERT INTO songs(id, title, artist, original_title, channel, duration_ms, status, added_by, "
            "media_version, size_bytes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                song_id,
                title,
                artist,
                f"{artist} – {title} (Official Video)",
                f"{artist} Official",
                duration_ms,
                status,
                added_by,
                1 if status == "ready" else 0,
                1234,
                created,
                created,
            ),
        )
    if files:
        write_media(app.state.config, song_id)


def write_media(config: Config, song_id: str) -> Path:
    d = config.songs_dir / song_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "instrumental.opus").write_bytes(bytes(range(256)) * 40)
    (d / "vocals.opus").write_bytes(b"V" * 5000)
    (d / "thumb.jpg").write_bytes(b"\xff\xd8\xff" + b"J" * 100)
    return d


def seed_job(
    app: Any, song_id: str, job_id: str = "j_1", stage: str = "separating", progress: float = 0.5
) -> None:
    with app.state.db.tx() as conn:
        conn.execute(
            "INSERT INTO jobs(id, kind, song_id, stage, progress, created_at, updated_at) "
            "VALUES (?, 'process', ?, ?, ?, '2026-10-04T00:00:00Z', '2026-10-04T00:00:00Z')",
            (job_id, song_id, stage, progress),
        )


class Actor:
    """Troca o principal resolvido por `authorize` (simula dois atores: host e guest:x)."""

    def __init__(self, monkeypatch: Any):
        self.p: Principal = HOST
        monkeypatch.setattr(security, "resolve_principal", lambda request: self.p)

    def host(self) -> None:
        self.p = HOST

    def guest(self) -> None:
        self.p = GUEST_X


def wait_for(pred: Any, timeout: float = 3.0, interval: float = 0.02) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = pred()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condição não satisfeita a tempo")


# ---- servidor real (SSE) -----------------------------------------------------------------------
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def live_server(tmp_path: Path):
    port = free_port()
    config = Config(port=port, data_dir=tmp_path / "live", device="cpu", open_browser=False)
    app = create_app(config)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_for(lambda: server.started, timeout=10)
    base = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(base_url=base, timeout=5) as client:
            yield app, client, base
    finally:
        server.should_exit = True
        thread.join(timeout=10)


class SSEReader:
    """Lê `/api/events` por um socket cru numa thread e entrega (event, data, t_recebido) numa fila.

    `close()` derruba o socket (simula a aba do navegador fechando)."""

    def __init__(self, base: str, client_id: str, role: str = "control"):
        self.events: queue.Queue[tuple[str, Any, float]] = queue.Queue()
        host, port = base.removeprefix("http://").split(":")
        self._sock = socket.create_connection((host, int(port)), timeout=30)
        request = (
            f"GET /api/events?client_id={client_id}&role={role} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\nAccept: text/event-stream\r\n\r\n"
        )
        self._sock.sendall(request.encode())
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        buf = b""
        event, data = None, []
        try:
            while True:
                chunk = self._sock.recv(65536)
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.decode("utf-8", "replace").rstrip("\r")
                    if line.startswith("event: "):
                        event = line[7:]
                    elif line.startswith("data: "):
                        data.append(line[6:])
                    elif line == "" and event:
                        self.events.put((event, json.loads("\n".join(data)), time.monotonic()))
                        event, data = None, []
        except OSError:
            return

    def next(self, type_: str, timeout: float = 3.0, where: Any = None) -> tuple[Any, float]:
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise AssertionError(f"evento {type_} não chegou")
            try:
                ev, data, t = self.events.get(timeout=left)
            except queue.Empty as exc:
                raise AssertionError(f"evento {type_} não chegou") from exc
            if ev == type_ and (where is None or where(data)):
                return data, t

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._sock.shutdown(socket.SHUT_RDWR)
        self._sock.close()
        self._thread.join(timeout=2)
