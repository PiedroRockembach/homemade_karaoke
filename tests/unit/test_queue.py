"""Regras da fila em nível de serviço: cantor, próximo item pronto, reação a eventos de jobs."""

from __future__ import annotations

import pytest

from karaoke.errors import ApiError
from karaoke.events import Event
from karaoke.services.queue import QueueService, clean_singer
from tests.api.core_helpers import SONG_A, SONG_B, SONG_C, seed_song


def test_clean_singer():
    assert clean_singer("  Ana   Maria ") == "Ana Maria"
    assert clean_singer("   ") is None
    assert clean_singer(None) is None
    with pytest.raises(ApiError):
        clean_singer("x" * 81)


def test_next_ready_skips_not_ready(app, client):
    client.patch("/api/settings", json={"autoplay": False})
    seed_song(app, SONG_A, status="processing")
    seed_song(app, SONG_B, status="queued")
    seed_song(app, SONG_C)
    svc: QueueService = app.state.queue
    for s in (SONG_A, SONG_B, SONG_C):
        svc.add(s, None, "end", "host")
    conn = app.state.db.connect()
    try:
        nxt = svc.next_ready(conn)
        assert nxt["song_id"] == SONG_C
        assert [r["song_id"] for r in svc.pending_rows(conn)] == [SONG_A, SONG_B, SONG_C]
    finally:
        conn.close()


def test_revision_increments_on_every_change(app, client):
    client.patch("/api/settings", json={"autoplay": False})
    seed_song(app, SONG_A)
    svc: QueueService = app.state.queue
    r0 = svc.snapshot()["revision"]
    item = svc.add(SONG_A, "Ana", "end", "host")
    r1 = svc.snapshot()["revision"]
    svc.update(item["id"], "Bia", "host")
    r2 = svc.snapshot()["revision"]
    assert r0 < r1 < r2


def test_on_event_marks_dirty_only_on_stage_change(app, client):
    svc: QueueService = app.state.queue
    job = {"id": "j_1", "song_id": SONG_A, "stage": "downloading", "progress": 0.1}
    svc.on_event(Event(1, "job.updated", job))
    assert svc._dirty == {SONG_A}
    svc._dirty.clear()
    svc.on_event(Event(2, "job.updated", {**job, "progress": 0.2}))  # só progresso: não republica a fila
    assert svc._dirty == set()
    svc.on_event(Event(3, "job.updated", {**job, "stage": "done"}))
    svc.on_event(Event(4, "library.updated", {"action": "added", "song_id": SONG_B}))
    assert svc._dirty == {SONG_A, SONG_B}
    svc.on_event(Event(5, "toast", "x"))
    svc.process_dirty()  # músicas fora da fila: nada a fazer, sem erro
    assert svc._dirty == set()
