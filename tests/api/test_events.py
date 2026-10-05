"""SSE `/api/events` com servidor real (uvicorn em thread) — contrato §10 e AC-07.2 (≤ 300 ms)."""

from __future__ import annotations

import time

import pytest

from tests.api.core_helpers import SONG_A, SONG_B, SSEReader, live_server, seed_song, wait_for


@pytest.fixture
def live(tmp_path):
    with live_server(tmp_path) as srv:
        yield srv


@pytest.fixture
def readers():
    opened: list[SSEReader] = []
    yield opened
    for r in opened:
        r.close()


def open_reader(readers, base, client_id, role="control"):
    r = SSEReader(base, client_id, role)
    readers.append(r)
    return r


def test_hello_snapshot(live, readers):
    app, client, base = live
    seed_song(app, SONG_A)
    client.post("/api/queue", json={"song_id": SONG_A, "singer": "Ana"})
    r = open_reader(readers, base, "c_ctrl1")
    hello, _ = r.next("hello")
    assert set(hello) == {
        "server_time",
        "client_id",
        "player",
        "queue",
        "jobs",
        "settings",
        "system",
        "guest_state",
    }
    assert hello["guest_state"]["enabled"] is False  # v0.3: começa desligado (AC-11.5)
    assert hello["client_id"] == "c_ctrl1"
    assert abs(hello["server_time"] - time.time() * 1000) < 5000
    assert hello["player"]["status"] == "waiting_gesture"
    assert hello["queue"]["current"]["singer"] == "Ana"
    assert hello["queue"]["current"]["can_remove"] is True
    assert hello["jobs"] == []
    assert hello["settings"]["countdown_s"] == 8
    assert hello["system"] == {"device": "cpu", "gpu_warning": None, "model_download": None}


def test_command_reflects_in_sse_under_300ms(live, readers):
    app, client, base = live
    seed_song(app, SONG_A)
    stage = open_reader(readers, base, "c_stage1", role="stage")
    stage.next("hello")
    assert client.post("/api/stage/claim", json={"client_id": "c_stage1"}).status_code == 200
    client.post("/api/queue", json={"song_id": SONG_A})
    st = client.get("/api/player").json()
    client.post(
        "/api/player/report",
        json={
            "client_id": "c_stage1",
            "queue_item_id": st["queue_item_id"],
            "song_id": SONG_A,
            "position_ms": 0,
            "playing": True,
            "event": "loaded",
        },
    )
    stage.next("player.state", where=lambda d: d["status"] == "playing")
    ctrl = open_reader(readers, base, "c_ctrl1")
    ctrl.next("hello")
    for action, expected in (("pause", "paused"), ("play", "playing"), ("toggle", "paused")):
        t0 = time.monotonic()
        r = client.post("/api/player/command", json={"action": action})
        assert r.status_code == 200
        for reader in (stage, ctrl):
            data, t = reader.next("player.state", where=lambda d, e=expected: d["status"] == e)
            assert t - t0 < 0.3, f"{action}: {int((t - t0) * 1000)} ms"


def test_queue_and_settings_events(live, readers):
    app, client, base = live
    seed_song(app, SONG_A)
    seed_song(app, SONG_B)
    client.patch("/api/settings", json={"autoplay": False})
    r = open_reader(readers, base, "c_ctrl1")
    r.next("hello")
    item = client.post("/api/queue", json={"song_id": SONG_A}).json()
    q, _ = r.next("queue.updated")
    assert [i["id"] for i in q["items"]] == [item["id"]] and q["items"][0]["can_remove"] is True
    client.patch("/api/settings", json={"countdown_s": 3})
    s, _ = r.next("settings.updated")
    assert s["countdown_s"] == 3
    client.patch(f"/api/songs/{SONG_B}", json={"offset_ms": 100})
    song, _ = r.next("song.updated")
    assert song["offset_ms"] == 100
    lib, _ = r.next("library.updated")
    assert lib["action"] == "updated" and lib["song_id"] == SONG_B
    client.delete(f"/api/songs/{SONG_B}")
    lib, _ = r.next("library.updated", where=lambda d: d["action"] == "removed")
    assert lib == {"action": "removed", "song_id": SONG_B, "song": None}


def test_stage_connection_and_owner_disconnect(live, readers):
    app, client, base = live
    app.state.player.OWNER_GRACE_MS = 200
    ctrl = open_reader(readers, base, "c_ctrl1")
    ctrl.next("hello")
    stage = SSEReader(base, "c_stage9", role="stage")
    stage.next("hello")
    st, _ = ctrl.next("player.state", where=lambda d: d["stage"]["connected"] == 1)
    client.post("/api/stage/claim", json={"client_id": "c_stage9"})
    ctrl.next("player.state", where=lambda d: d["stage"]["audio_owner"] == "c_stage9")
    stage.close()  # Palco dono cai
    st, _ = ctrl.next("player.state", where=lambda d: d["stage"]["audio_owner"] is None, timeout=5)
    assert st["stage"]["connected"] == 0 and st["stage"]["unlocked"] is False


def test_job_events_refresh_queue(live, readers):
    """Quando o JobRunner (T-006) publica library.updated, a fila é republicada e o player inicia."""
    app, client, base = live
    seed_song(app, SONG_A, status="processing")
    client.post("/api/queue", json={"song_id": SONG_A})
    r = open_reader(readers, base, "c_ctrl1")
    r.next("hello")
    with app.state.db.tx() as conn:
        conn.execute("UPDATE songs SET status = 'ready', media_version = 1 WHERE id = ?", (SONG_A,))
    app.state.bus.publish("library.updated", {"action": "added", "song_id": SONG_A, "song": None})
    q, _ = r.next("queue.updated", where=lambda d: d["current"] is not None)
    assert q["current"]["ready"] is True
    wait_for(lambda: client.get("/api/player").json()["status"] == "waiting_gesture")


def test_invalid_role(live):
    _, client, _ = live
    # v0.3: role=guest existe, mas é exclusivo de convidados (host → 403 forbidden)
    r = client.get("/api/events?role=guest")
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert client.get("/api/events?role=admin").status_code == 400
    assert client.get("/api/events?client_id=bad id").status_code == 400


# ---- QA H-01 (parte backend): o encerramento fecha os streams SSE na hora -------------------------------------
def _start_live(tmp_path):
    """Servidor real em thread, controlado pelo teste (o fixture `live` encerra sozinho)."""
    import threading

    import uvicorn

    from karaoke.app import create_app
    from karaoke.config import Config
    from tests.api.core_helpers import free_port

    port = free_port()
    app = create_app(Config(port=port, data_dir=tmp_path / "live", device="cpu", open_browser=False))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_for(lambda: server.started, timeout=10)
    return app, server, thread, f"http://127.0.0.1:{port}"


def test_bus_shutdown_ends_open_streams_and_server_exits_fast(tmp_path, readers):
    app, server, thread, base = _start_live(tmp_path)
    try:
        ctrl = open_reader(readers, base, "c_ctrl_sd")
        stage = open_reader(readers, base, "c_stage_sd", role="stage")
        ctrl.next("hello")
        stage.next("hello")
        t0 = time.monotonic()
        # o que o `handle_exit` do uvicorn faz no Ctrl+C (contrato com o __main__): avisar o bus e pedir a saída
        app.state.bus.shutdown()
        server.should_exit = True
        thread.join(timeout=5)
        elapsed = time.monotonic() - t0
        assert not thread.is_alive(), "servidor não encerrou com streams SSE abertos"
        assert elapsed < 2.0, elapsed
        # os dois streams terminaram do lado do servidor (EOF no socket)
        wait_for(lambda: not ctrl._thread.is_alive() and not stage._thread.is_alive(), timeout=2)
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_subscribe_after_shutdown_ends_immediately(tmp_path):
    app, server, thread, base = _start_live(tmp_path)
    try:
        import httpx

        app.state.bus.shutdown()
        t0 = time.monotonic()
        with (
            httpx.Client(base_url=base, timeout=3) as c,
            c.stream("GET", "/api/events?client_id=c_late&role=stage") as r,
        ):
            body = r.read().decode()  # termina: o servidor fecha o stream logo após o `retry`
        assert time.monotonic() - t0 < 1.5
        assert body.startswith("retry: ")
        assert app.state.bus.count() == 0
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_bus_shutdown_is_idempotent_and_thread_safe(tmp_path):
    import asyncio
    import threading

    from karaoke.events import EventBus, sse_stream

    async def main():
        bus = EventBus()
        bus.bind_loop()
        sub = bus.subscribe("c_x", "control")
        chunks = []

        async def consume():
            async for c in sse_stream(bus, sub, keepalive_s=30):
                chunks.append(c)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        threading.Thread(target=bus.shutdown).start()  # de outra thread, como um handler de sinal
        await asyncio.wait_for(task, timeout=1)
        bus.shutdown()  # idempotente
        assert bus.closing and bus.count() == 0
        assert chunks == ["retry: 1000\n\n"]

    asyncio.run(main())


# ---- review M2: I/O de banco nas rotas não congela o event loop ---------------------------------------------
def test_db_write_lock_does_not_freeze_event_loop(live, readers):
    """Com o lock de escrita do SQLite preso por outra thread, rotas que escrevem (retry de job, letra manual)
    esperam no threadpool; o loop segue servindo SSE e outras rotas."""
    import threading

    import httpx

    app, client, base = live
    seed_song(app, SONG_A)
    ctrl = open_reader(readers, base, "c_ctrl_lock")
    ctrl.next("hello")
    hold = threading.Event()
    release = threading.Event()

    def holder():
        with app.state.db.tx():  # BEGIN IMMEDIATE: segura o lock de escrita
            hold.set()
            release.wait(5)

    threading.Thread(target=holder, daemon=True).start()
    assert hold.wait(2)
    results = {}

    def call(name, method, url, **kw):
        with httpx.Client(base_url=base, timeout=15) as c:
            results[name] = c.request(method, url, **kw).status_code

    lrc = "[00:01.00]linha um\n[00:05.00]linha dois\n"
    workers = [
        threading.Thread(target=call, args=("retry", "POST", "/api/jobs/j_999/retry")),
        threading.Thread(
            target=call,
            args=("manual", "POST", f"/api/songs/{SONG_A}/lyrics/manual"),
            kwargs={"json": {"lrc": lrc}},
        ),
    ]
    for w in workers:
        w.start()
    time.sleep(0.3)  # as duas requisições já estão esperando o lock
    try:
        t0 = time.monotonic()
        assert client.get("/api/health").status_code == 200
        assert time.monotonic() - t0 < 0.5
        r2 = open_reader(readers, base, "c_ctrl_lock2")
        r2.next("hello", timeout=1.0)  # um novo stream abre normalmente
    finally:
        release.set()
    for w in workers:
        w.join(15)
    assert results == {"retry": 404, "manual": 201}
