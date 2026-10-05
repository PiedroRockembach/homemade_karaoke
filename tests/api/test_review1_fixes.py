"""Regressões da review-1 (FIX-001): H-1 (limite de corpo), M-1 (sessões inativas; sem limite por IP desde a
DECISION 0016), M-2 (job + item numa transação), L-2 (troca de sessão fecha SSE), L-4 (estáticos com balde
próprio), L-5 (delete na transação).
M-3 (conexões do listener da LAN) fica em tests/unit/test_guest_listener.py, porque precisa de socket real."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from karaoke import bodylimit, security
from karaoke import youtube as yt
from karaoke.app import create_app
from karaoke.errors import ApiError
from karaoke.guest import service as guest_service
from karaoke.security import HOST, Principal
from tests.api.core_helpers import SONG_A, seed_song
from tests.conftest import BASE_URL

BIA = Principal(role="guest", id="g_1", name="Bia")


@pytest.fixture
def app(config):
    a = create_app(config)
    a.state.runner_autostart = False
    return a


def code(r) -> str:
    return r.json()["error"]["code"]


# ---- H-1: corpo limitado antes de qualquer leitura -----------------------------------------------------------
def test_lan_content_length_over_limit_is_413_before_authorize(guest_env):
    guest_env.enable()
    lan = guest_env.lan()
    big = b'{"token":"' + b"x" * (bodylimit.LAN_MAX_BODY + 1) + b'"}'
    r = lan.post("/api/guest/session", content=big, headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and code(r) == "payload_too_large"
    assert r.json()["error"]["details"] == {"max_bytes": bodylimit.LAN_MAX_BODY}
    # nem Host forjado nem rota host mudam o resultado: o limite vem antes do authorize
    r = lan.patch("/api/settings", content=big, headers={"Content-Type": "application/json", "Host": "x"})
    assert r.status_code == 413
    assert r.headers.get("x-content-type-options") == "nosniff"  # headers de segurança também no 413


def test_lan_chunked_body_over_limit_is_413(guest_env):
    guest_env.enable()
    lan = guest_env.lan()

    def chunks():
        for _ in range(40):
            yield b"x" * 1024

    r = lan.post("/api/guest/session", content=chunks(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and code(r) == "payload_too_large"


def test_lan_small_body_still_works(guest_env):
    guest_env.enable()
    r = guest_env.lan().post("/api/guest/session", json={"token": guest_env.token(), "name": "Bia"})
    assert r.status_code == 201


def test_host_limit_is_generous_but_bounded(client):
    lrc = "[00:01.00] " + "á" * 199_000  # colagem de LRC no teto do campo (≈ 400 KB em UTF-8)
    r = client.post(f"/api/songs/{SONG_A}/lyrics/manual", json={"lrc": lrc})
    assert r.status_code != 413
    r = client.patch(
        "/api/settings",
        content=b"x" * (bodylimit.HOST_MAX_BODY + 1),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413 and r.json()["error"]["details"]["max_bytes"] == bodylimit.HOST_MAX_BODY


def test_middleware_stops_reading_chunked_body_at_limit():
    """Sem Content-Length: o app interno não consegue ler além do limite (memória limitada)."""
    reads = 0

    async def inner(scope, receive, send):
        nonlocal reads
        more = True
        while more:
            msg = await receive()
            reads += 1
            more = msg.get("more_body", False)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        return {"type": "http.request", "body": b"x" * 1024, "more_body": True}  # infinito

    sent: list[dict] = []

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "client": ("192.168.1.50", 1), "headers": [(b"transfer-encoding", b"chunked")]}
    asyncio.run(bodylimit.BodyLimitMiddleware(inner)(scope, receive, send))
    assert reads == bodylimit.LAN_MAX_BODY // 1024  # a leitura seguinte, que passaria do limite, é abortada
    assert sent[0]["status"] == 413 and len(sent) == 2


# ---- M-1: um convidado não esgota MAX_SESSIONS ---------------------------------------------------------------
def _new_session(env, ip: str, name: str = "X"):
    return env.lan(ip).post("/api/guest/session", json={"token": env.token(), "name": name})


def test_many_guests_behind_one_ip_all_join(guest_env):
    """DECISION 0016 / qa-2 N-1: sem limite por IP. 8 convidados atrás do mesmo NAT entram e ninguém é despejado."""
    guest_env.enable()
    guests = [guest_env.join(f"N{i}", ip="192.168.1.66") for i in range(8)]
    assert guest_env.service.session_count == 8
    assert all(g.get("/api/queue").status_code == 200 for g in guests)


def test_replacing_with_valid_cookie_replaces_session(guest_env):
    guest_env.enable()
    guest = guest_env.join("Bia", ip="192.168.1.66")
    r = guest.post("/api/guest/session", json={"token": guest_env.token(), "name": "Bia 2"})
    assert r.status_code == 201  # substituição (api.md §12.3)
    assert guest_env.service.session_count == 1


def test_inactive_sessions_do_not_count(guest_env, monkeypatch):
    monkeypatch.setattr(guest_service, "MAX_SESSIONS", 2)
    svc = guest_env.service
    now = [1_000_000.0]
    svc.clock = lambda: now[0]
    guest_env.enable()
    a = guest_env.join("A", ip="192.168.1.80")
    guest_env.join("B", ip="192.168.1.81")
    assert _new_session(guest_env, "192.168.1.82").status_code == 429  # 2 ativas
    now[0] += guest_service.SESSION_IDLE_S + 1
    a_id = next(s.id for s in svc.sessions() if s.name == "A")
    sub = guest_env.app.state.bus.subscribe("c_a", "guest", Principal(role="guest", id=a_id, name="A"))
    try:
        assert _new_session(guest_env, "192.168.1.83", "C").status_code == 201  # B inativa; A tem SSE aberto
        assert _new_session(guest_env, "192.168.1.84", "D").status_code == 429  # A (SSE) + C ativas
    finally:
        guest_env.app.state.bus.unsubscribe(sub)
    assert a.get("/api/queue").status_code == 200  # inativa não é apagada: volta a valer


# ---- L-2: trocar de sessão fecha os SSE da antiga -------------------------------------------------------------
def test_replacing_session_closes_old_streams(guest_env):
    guest_env.enable()
    guest = guest_env.join("Bia")
    old_id = guest.get("/api/guest/session").json()["guest"]["id"]
    bus = guest_env.app.state.bus
    sub = bus.subscribe("c_old", "guest", Principal(role="guest", id=old_id, name="Bia"))
    assert bus.count_principal(old_id, "guest") == 1
    r = guest.post("/api/guest/session", json={"token": guest_env.token(), "name": "Bia 2"})
    assert r.status_code == 201
    assert bus.count_principal(old_id, "guest") == 0
    assert sub.queue.get_nowait() is None  # o stream antigo recebe o fim


# ---- L-4: estáticos não gastam `read`/`anon` --------------------------------------------------------------------
def test_static_files_do_not_hit_anon_bucket(guest_env):
    guest_env.enable()
    lan = guest_env.lan("192.168.1.95")
    for _ in range(5):  # 5 recargas da página com ~20 arquivos
        assert lan.get("/guest").status_code == 200
        for _ in range(19):
            assert lan.get("/static/css/guest.css").status_code == 200
    assert lan.get("/api/guest/session").status_code == 401  # e não 429: o balde `anon` está intacto


# ---- M-2: job + item numa transação (sem cota desde a DECISION 0016) -------------------------------------------
class As:
    def __init__(self, app):
        self.p: Principal = HOST

        def fake_authorize(request: Request) -> Principal:
            request.state.principal = self.p
            return self.p

        app.dependency_overrides[security.authorize] = fake_authorize


def test_guest_job_rolls_back_when_enqueue_fails(app, monkeypatch):
    yt._recent.clear()
    with TestClient(app, base_url=BASE_URL, client=("127.0.0.1", 50000)) as client:
        assert client.patch("/api/settings", json={"autoplay": False}).status_code == 200
        As(app).p = BIA
        monkeypatch.setattr(
            yt, "probe", lambda vid: yt.SearchResult(vid, f"Artist - {vid}", "Ch", 180_000, False)
        )

        def boom(*_a, **_k):
            raise ApiError(409, "song_not_ready", "x", {})

        monkeypatch.setattr(app.state.queue, "add_in_txn", boom)
        assert client.post("/api/jobs", json={"youtube_id": SONG_A}).status_code == 409
        conn = app.state.db.connect()
        try:
            jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            songs = conn.execute("SELECT COUNT(*) FROM songs WHERE id = ?", (SONG_A,)).fetchone()[0]
        finally:
            conn.close()
        assert jobs == 0 and songs == 0  # rollback: nenhum job órfão
    yt._recent.clear()


# ---- L-5: song_queued_by_others dentro da transação do delete ---------------------------------------------------
def test_library_delete_checks_queued_by_others_itself(client, app):
    seed_song(app, SONG_A)
    with app.state.db.tx() as conn:
        conn.execute(
            "INSERT INTO queue_items(id, song_id, singer, status, sort_key, added_by, added_by_name, added_at) "
            "VALUES ('q_9', ?, 'Bia', 'pending', 1, 'g_1', 'Bia', '2026-10-05T00:00:00Z')",
            (SONG_A,),
        )
    with pytest.raises(ApiError) as exc:
        app.state.library.delete(SONG_A, "host")
    assert exc.value.code == "song_queued_by_others" and exc.value.details["count"] == 1
