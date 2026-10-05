"""Contrato front × back do modo convidado (T-007): os campos que o JS lê existem nas respostas **reais**.

As listas abaixo foram extraídas à mão (grep de `obj.campo`) de:
- `web/js/guest/{main,session,search,request-sheet,my-queue,logic}.js` (página `/guest`, bloco C);
- `web/js/control/guest-panel.js` + `web/js/shared/format.js::fmtAddress` (Controle, bloco B);
- `web/js/stage/guest-qr.js` + `stage/main.js`/`screens.js` (`up_next`, bloco B);
- `web/js/shared/state.js` (`hello.guest_state`, `hello.guest`).
Se o JS passar a ler um campo novo, acrescente aqui. Sem rede/GPU.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from karaoke import youtube as yt
from karaoke.api.events import hello_snapshot
from karaoke.app import create_app
from karaoke.security import HOST, Principal
from tests.api.core_helpers import SONG_A, SONG_B, SONG_C, seed_song

NEW_ID = "aaaaaaaaaaa"

# ---- campos consumidos pelo JS ---------------------------------------------------------------------------
GUEST_STATE_KEYS = {  # guest-panel.js, guest-qr.js, stage/main.js
    "enabled",
    "url",
    "display_url",
    "qr",
    "sessions",
    "connected",
    "address",
    "port",
    "addresses",
    "firewall_hint",
    "error",
}
ADDRESS_KEYS = {"address", "interface", "kind", "recommended"}  # fmtAddress + seletor de IP
FIREWALL_KEYS = {"tool", "command"}  # guest-panel.js: hint.command
GUEST_ME_KEYS = {"id", "name", "limits"}  # session.js / logic.js
LIMIT_KEYS = {"max_duration_ms"}  # logic.js / search.js (sem cotas: DECISION 0016)
GUEST_HELLO_KEYS = {"guest", "player", "queue", "jobs"}  # main.js / state.js
HOST_HELLO_KEYS = {"guest_state"}  # state.js
QUEUE_KEYS = {"current", "items"}  # main.js / my-queue.js
QUEUE_ITEM_KEYS = {  # my-queue.js, queue-list.js, format.js::requestedByText
    "id",
    "song_id",
    "singer",
    "ready",
    "song",
    "job",
    "added_by",
    "added_by_name",
    "can_remove",
}
QUEUE_SONG_KEYS = {"title", "artist", "thumbnail", "status"}  # item.song.*
ITEM_JOB_KEYS = {"stage", "progress"}  # item.job.*
JOB_KEYS = {"id", "song_id", "stage", "progress", "lyrics_source"}  # my-queue.js::jobFor (job.updated)
PLAYER_KEYS = {"status", "up_next"}  # state.player.status (guest/main.js), stage
UP_NEXT_KEYS = {"queue_item_id", "song", "singer", "added_by_name", "starts_at"}  # stage/screens.js
LIB_SONG_KEYS = {"id", "title", "artist", "duration_ms", "thumbnail"}  # search.js (res.songs[])
SEARCH_KEYS = {"results"}  # search.js
SEARCH_RESULT_KEYS = {  # search.js / logic.js::resultKind
    "youtube_id",
    "title",
    "channel",
    "duration_ms",
    "thumbnail",
    "is_long",
    "is_live",
    "library",
}
JOB_RESPONSE_KEYS = {"queue_item"}  # request-sheet.js: res.queue_item
SESSION_RESPONSE_KEYS = {"guest"}  # session.js: res.guest
LONG_DETAILS = {"duration_ms", "max_ms"}  # logic.js::errorMessage
RATE_DETAILS = {"retry_after_s", "reason"}  # logic.js::retryAfter
SSE_EVENT_TYPES = {"hello", "queue.updated", "player.state", "job.updated", "guest.ended", "guest.state"}


def missing(keys: set[str], obj: dict) -> set[str]:
    return keys - set(obj)


@pytest.fixture
def app(config):
    a = create_app(config)
    a.state.runner_autostart = False
    return a


@pytest.fixture
def fake_yt(monkeypatch):
    yt._recent.clear()
    results = [
        yt.SearchResult(SONG_A, "Queen – Bohemian Rhapsody", "Queen Official", 359_000, False),
        yt.SearchResult(NEW_ID, "Artist - Song", "Artist", 180_000, False),
    ]
    monkeypatch.setattr(yt, "search", lambda q, limit=15: list(results))
    monkeypatch.setattr(yt, "fetch_info", lambda vid: next(r for r in results if r.youtube_id == vid))
    yield
    yt._recent.clear()


def test_frontend_fields_exist_in_real_responses(guest_env, fake_yt):
    env = guest_env
    host = env.host
    assert host.patch("/api/settings", json={"autoplay": False}).status_code == 200
    seed_song(env.app, SONG_A, files=True)
    seed_song(env.app, SONG_B, files=True)
    seed_song(env.app, SONG_C, files=True)

    st = env.enable()
    assert not missing(GUEST_STATE_KEYS, st)
    assert st["addresses"] and not missing(ADDRESS_KEYS, st["addresses"][0])
    if st["firewall_hint"] is not None:
        assert not missing(FIREWALL_KEYS, st["firewall_hint"])

    lan = env.lan()
    r = lan.post("/api/guest/session", json={"token": env.token(), "name": "Bia"})
    assert not missing(SESSION_RESPONSE_KEYS, r.json())
    me = r.json()["guest"]
    assert not missing(GUEST_ME_KEYS, me) and not missing(LIMIT_KEYS, me["limits"])
    assert not missing(SESSION_RESPONSE_KEYS, lan.get("/api/guest/session").json())

    songs = lan.get("/api/songs", params={"status": "ready", "sort": "title"}).json()["songs"]
    assert not missing(LIB_SONG_KEYS, songs[0])
    res = lan.get("/api/search", params={"q": "x", "limit": 15}).json()
    assert not missing(SEARCH_KEYS, res) and not missing(SEARCH_RESULT_KEYS, res["results"][0])

    item = lan.post("/api/queue", json={"song_id": SONG_A, "singer": "Ana"}).json()
    assert not missing(QUEUE_ITEM_KEYS, item) and not missing(QUEUE_SONG_KEYS, item["song"])

    jr = lan.post("/api/jobs", json={"youtube_id": NEW_ID, "singer": "Bia"}).json()
    assert not missing(JOB_RESPONSE_KEYS, jr) and not missing(JOB_KEYS, jr["job"])
    assert jr["queue_item"]["job"] is not None and not missing(ITEM_JOB_KEYS, jr["queue_item"]["job"])

    q = lan.get("/api/queue").json()
    assert not missing(QUEUE_KEYS, q)

    p = Principal(role="guest", id=me["id"], name="Bia")
    g_hello = hello_snapshot(env.app, "c_g", p.id, p)
    assert not missing(GUEST_HELLO_KEYS, g_hello) and not missing(GUEST_ME_KEYS, g_hello["guest"])
    assert not missing(PLAYER_KEYS, g_hello["player"])
    assert not missing(HOST_HELLO_KEYS, hello_snapshot(env.app, "c_h", "host", HOST))

    # up_next com item de convidado (mesma técnica de test_guest_queue::test_up_next_has_added_by_name)
    player = env.app.state.player
    player.s["up_next"] = {"queue_item_id": item["id"], "starts_at": None}
    conn = env.app.state.db.connect()
    try:
        up = player.state_conn(conn, "host")["up_next"]
    finally:
        conn.close()
        player.s["up_next"] = None
    assert not missing(UP_NEXT_KEYS, up) and up["added_by_name"] == "Bia"

    # DECISION 0016: sem cota, o 3º e o 4º pedido entram
    assert lan.post("/api/queue", json={"song_id": SONG_B}).status_code == 201
    assert lan.post("/api/queue", json={"song_id": SONG_C}).status_code == 201


def test_long_and_rate_limit_details(guest_env, fake_yt, monkeypatch):
    env = guest_env
    env.enable()
    lan = env.join()
    long = yt.SearchResult("bbbbbbbbbbb", "Long", "Band", 1_260_000, False)
    monkeypatch.setattr(yt, "fetch_info", lambda vid: long)
    r = lan.post("/api/jobs", json={"youtube_id": "bbbbbbbbbbb"})
    assert r.json()["error"]["code"] == "long_video_not_allowed"
    assert not missing(LONG_DETAILS, r.json()["error"]["details"])
    codes = [lan.get("/api/search", params={"q": "x"}) for _ in range(12)]
    r429 = next(r for r in codes if r.status_code == 429)
    assert r429.json()["error"]["code"] == "rate_limited"
    assert not missing(RATE_DETAILS, r429.json()["error"]["details"])
    assert r429.json()["error"]["details"]["reason"] == "search"
    assert r429.headers["Retry-After"] == str(r429.json()["error"]["details"]["retry_after_s"])  # FIX-005


def test_sse_event_names_used_by_js_are_known():
    """`js/shared/events.js` registra os tipos; o backend os conhece (ROLES/filtros)."""
    from karaoke import events as ev

    known = set(getattr(ev, "GUEST_EVENTS", set())) | set(getattr(ev, "GUEST_JOB_EVENTS", set()))
    known |= set(getattr(ev, "GUEST_ONLY_EVENTS", set())) | {"hello", "guest.state"}
    assert known >= SSE_EVENT_TYPES, SSE_EVENT_TYPES - known
    js = (Path(ev.__file__).parent / "web/js/shared/events.js").read_text(encoding="utf-8")
    for t in ("guest.state", "guest.ended", "queue.updated", "player.state", "job.updated"):
        assert f'"{t}"' in js, t
