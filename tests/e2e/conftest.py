"""Fixtures dos testes e2e (Playwright). Dono: qa-engineer (T-009).

- `live`: sobe `python -m karaoke` de verdade (subprocesso, porta livre em 8840–8849, data dir temporário,
  `KARAOKE_DEVICE=cpu`, sem abrir navegador) e devolve um objeto com helpers de semente/HTTP.
- `browser`: Chromium do Playwright usando o Chrome do sistema (`KARAOKE_E2E_CHROME`, padrão
  `/usr/bin/google-chrome-stable`) — não baixa navegadores. Se nada existir, os testes são pulados.

As músicas semeadas têm áudio Opus real gerado pelo ffmpeg (tom senoidal), para o Palco decodificar de verdade.
Rode com: `.venv/bin/python -m pytest -m e2e tests/e2e`.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
PORTS = range(8840, 8850)
CHROME_CANDIDATES = [
    os.environ.get("KARAOKE_E2E_CHROME", ""),
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]


_port_cursor = 0


def _free_port() -> int:
    """Porta livre em 8840–8849, em rodízio. `SO_REUSEADDR` igual ao uvicorn/`pick_port`: portas em TIME-WAIT
    contam como livres (sem ele a faixa esgota após algumas execuções seguidas); um listener ativo continua
    contando como ocupado. O rodízio evita reusar a porta do teste anterior logo em seguida."""
    global _port_cursor
    ports = list(PORTS)
    for i in range(len(ports)):
        port = ports[(_port_cursor + i) % len(ports)]
        with socket.socket() as s:
            if os.name == "posix":
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
        _port_cursor = (_port_cursor + i + 1) % len(ports)
        return port
    raise RuntimeError("nenhuma porta livre em 8840–8849")


def make_opus(path: Path, seconds: float, freq: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:duration={seconds}:sample_rate=48000",
            "-ac",
            "2",
            "-c:a",
            "libopus",
            "-b:a",
            "48k",
            str(path),
        ],
        check=True,
    )


def make_jpg(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x6644aa:s=320x180:d=1",
            "-frames:v",
            "1",
            str(path),
        ],
        check=True,
    )


class Live:
    def __init__(self, base: str, data_dir: Path, proc: subprocess.Popen):
        self.base = base
        self.data_dir = data_dir
        self.proc = proc
        self.http = httpx.Client(base_url=base, timeout=10, headers={"Origin": base})

    # ---- semente direta no banco (antes de abrir as páginas) ----------------------------------
    def db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.data_dir / "karaoke.db", timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def seed_song(
        self,
        song_id: str,
        title: str,
        artist: str = "Artista Teste",
        seconds: float = 12.0,
        freq: int = 440,
        added_by: str = "host",
        lrc: str | None = None,
    ) -> None:
        d = self.data_dir / "songs" / song_id
        make_opus(d / "instrumental.opus", seconds, freq)
        make_opus(d / "vocals.opus", seconds, freq * 2)
        make_jpg(d / "thumb.jpg")
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        size = sum(f.stat().st_size for f in d.iterdir())
        conn = self.db()
        try:
            conn.execute(
                "INSERT INTO songs(id,title,artist,original_title,channel,duration_ms,status,added_by,"
                "media_version,size_bytes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    song_id,
                    title,
                    artist,
                    f"{artist} - {title}",
                    artist,
                    int(seconds * 1000),
                    "ready",
                    added_by,
                    1,
                    size,
                    now,
                    now,
                ),
            )
        finally:
            conn.close()
        if lrc:
            r = self.http.post(f"/api/songs/{song_id}/lyrics/manual", json={"lrc": lrc})
            assert r.status_code == 201, r.text

    def seed_queue_item_as(self, song_id: str, added_by: str, singer: str | None = None) -> None:
        """Insere item de fila de outro ator direto no banco (simula convidado; v1 só tem `host`)."""
        conn = self.db()
        try:
            n = conn.execute("SELECT COALESCE(MAX(sort_key),0)+1 FROM queue_items").fetchone()[0]
            qid = conn.execute(
                "INSERT INTO counters(name,value) VALUES('queue_item',1) "
                "ON CONFLICT(name) DO UPDATE SET value=value+1 RETURNING value"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO queue_items(id,song_id,singer,status,sort_key,added_by,added_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (f"q_{qid}", song_id, singer, "pending", n, added_by, "2026-10-04T00:00:00Z"),
            )
        finally:
            conn.close()

    # ---- API ----------------------------------------------------------------------------------
    def get(self, path: str) -> Any:
        r = self.http.get(path)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict | None = None) -> httpx.Response:
        return self.http.post(path, json=body or {})

    def enqueue(self, song_id: str, singer: str | None = None) -> dict:
        r = self.post("/api/queue", {"song_id": song_id, "singer": singer})
        assert r.status_code == 201, r.text
        return r.json()

    def command(self, action: str, value: Any = None) -> httpx.Response:
        body: dict[str, Any] = {"action": action}
        if value is not None:
            body["value"] = value
        return self.post("/api/player/command", body)

    def player(self) -> dict:
        return self.get("/api/player")

    def wait(self, pred: Any, timeout: float = 8.0, msg: str = "condição") -> Any:
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = pred()
            if last:
                return last
            time.sleep(0.05)
        raise AssertionError(f"{msg} não satisfeita em {timeout}s (último: {last!r})")


def start_server(data_dir: Path, port: int | None = None, log: Path | None = None) -> Live:
    port = port or _free_port()
    env = dict(os.environ)
    env.update(
        KARAOKE_PORT=str(port),
        KARAOKE_DATA_DIR=str(data_dir),
        KARAOKE_NO_BROWSER="1",
        KARAOKE_DEVICE="cpu",
        PATH=f"{Path(sys.executable).parent}:{env.get('PATH', '')}",
    )
    out = open(log or data_dir.parent / f"server-{port}.log", "ab")  # noqa: SIM115
    proc = subprocess.Popen([sys.executable, "-m", "karaoke"], cwd=ROOT, env=env, stdout=out, stderr=out)
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"servidor saiu com código {proc.returncode}")
        with contextlib.suppress(httpx.HTTPError):
            if httpx.get(f"{base}/api/health", timeout=1).status_code == 200:
                return Live(base, data_dir, proc)
        time.sleep(0.2)
    proc.terminate()
    raise RuntimeError("servidor não respondeu em 30 s")


def stop_server(live: Live) -> None:
    live.http.close()
    if live.proc.poll() is None:
        live.proc.terminate()
        try:
            live.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            live.proc.kill()
            live.proc.wait()


@pytest.fixture
def live(tmp_path: Path) -> Iterator[Live]:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg ausente")
    server = start_server(tmp_path / "data")
    try:
        yield server
    finally:
        stop_server(server)


def _chrome() -> str | None:
    for c in CHROME_CANDIDATES:
        if c and Path(c).exists():
            return c
    return None


@pytest.fixture(scope="session")
def browser() -> Iterator[Any]:
    sync_api = pytest.importorskip("playwright.sync_api")
    exe = _chrome()
    with sync_api.sync_playwright() as p:
        kwargs: dict[str, Any] = {"headless": os.environ.get("KARAOKE_E2E_HEADED") != "1"}
        if exe:
            kwargs["executable_path"] = exe
        try:
            b = p.chromium.launch(**kwargs)
        except Exception as exc:  # pragma: no cover - ambiente sem navegador
            pytest.skip(f"navegador indisponível: {exc}")
        yield b
        b.close()


@pytest.fixture(autouse=True)
def _close_leaked_contexts(request: pytest.FixtureRequest) -> Iterator[None]:
    """Fecha todo contexto de navegador aberto durante o teste (ex.: `open_stage`). Uma aba de Palco vazada
    reconecta pelo EventSource quando outro servidor sobe na mesma porta e rouba a posse do áudio."""
    if "browser" not in request.fixturenames:
        yield
        return
    b = request.getfixturevalue("browser")
    before = set(map(id, b.contexts))
    yield
    for ctx in list(b.contexts):
        if id(ctx) not in before:
            with contextlib.suppress(Exception):
                ctx.close()


@pytest.fixture
def context(browser: Any) -> Iterator[Any]:
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="pt-BR")
    yield ctx
    ctx.close()


def collect_external_requests(page: Any, base: str, bucket: list[str]) -> None:
    """Registra toda request que não vai para a própria origem (prova local-first no navegador)."""

    def on_request(req: Any) -> None:
        url = req.url
        if not (url.startswith(base) or url.startswith("data:") or url.startswith("blob:")):
            bucket.append(url)

    page.on("request", on_request)


def dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)[:2000]
