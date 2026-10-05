"""Fluxo ponta a ponta do modo convidado com o backend real integrado (T-007, QA v0.3).

Blocos A1 (sessão/autorização/SSE), A2 (fila/jobs/cotas), B (Controle/Palco) e C (`/guest`) foram feitos em paralelo
contra o contrato; aqui eles rodam juntos, **sem override do `authorize`**: o convidado é um `TestClient` que chega
"pelo listener da LAN" (`guest_env.lan()`, cliente 192.168.1.x), com cookie real de sessão.

O "SSE" é observado no `EventBus` real: assinaturas `control` e `guest` recebem exatamente o que o stream enviaria
(o mesmo `_deliver` com `visible_to` + `personalize`). Sem rede, sem GPU, sem sockets.
"""

from __future__ import annotations

import asyncio

import pytest

from karaoke import youtube as yt
from karaoke.api.events import hello_snapshot
from karaoke.app import create_app
from karaoke.events import Subscription
from karaoke.security import HOST, Principal
from tests.api.core_helpers import SONG_A, SONG_B, SONG_C, SONG_D, seed_song, write_media

NEW_ID = "aaaaaaaaaaa"
LONG_ID = "bbbbbbbbbbb"


@pytest.fixture
def app(config):
    a = create_app(config)
    a.state.runner_autostart = False  # job criado fica `queued`: nada de download/GPU
    return a


@pytest.fixture(autouse=True)
def _clean_cache():
    yt._recent.clear()
    yield
    yt._recent.clear()


@pytest.fixture
def fake_yt(monkeypatch):
    results = [
        yt.SearchResult(
            SONG_A, "Queen – Bohemian Rhapsody (Official Video)", "Queen Official", 359_000, False
        ),
        yt.SearchResult(NEW_ID, "Artist - Song", "Artist", 180_000, False),
        yt.SearchResult(LONG_ID, "Long – Concert", "Band", 1_260_000, False),
    ]
    monkeypatch.setattr(yt, "search", lambda q, limit=15: list(results))
    monkeypatch.setattr(yt, "fetch_info", lambda vid: next(r for r in results if r.youtube_id == vid))
    return results


def code(r) -> str:
    return r.json()["error"]["code"]


def subscribe(env, role: str, principal: Principal) -> Subscription:
    async def go():
        return env.app.state.bus.subscribe(f"c_{role}_{principal.id}", role, principal)

    return env.host.portal.call(go)


def drain(env, sub: Subscription) -> list[tuple[str, object]]:
    async def go():
        await asyncio.sleep(0)  # deixa rodar os `call_soon_threadsafe` pendentes
        out = []
        while not sub.queue.empty():
            ev = sub.queue.get_nowait()
            if ev is not None:
                out.append((ev.type, ev.data))
        return out

    return env.host.portal.call(go)


def last(events, type_):
    found = [d for t, d in events if t == type_]
    assert found, f"nenhum {type_} em {[t for t, _ in events]}"
    return found[-1]


def test_guest_flow_end_to_end(guest_env, fake_yt):
    env = guest_env
    host = env.host
    assert host.patch("/api/settings", json={"autoplay": False}).status_code == 200
    for sid, title in (
        (SONG_A, "Bohemian Rhapsody"),
        (SONG_B, "Evidências"),
        (SONG_C, "Zombie"),
        (SONG_D, "Gangnam"),
    ):
        seed_song(env.app, sid, title=title, files=True)

    # AC-11.1 / 11.5: começa desligado e a LAN não alcança nada
    st0 = host.get("/api/guest/state").json()
    assert st0["enabled"] is False and st0["url"] is None
    r = env.lan().get("/api/queue")
    assert r.status_code == 403  # Host do IP da LAN ainda não é permitido (host_not_allowed)

    # AC-11.2: enable sem reiniciar → GuestState com link, sessões e conectados
    st = env.enable()
    assert st["enabled"] and st["sessions"] == 0 and st["connected"] == 0
    token = host.get("/api/guest/state").json()["url"].split("#t=")[1]

    # AC-13.1 / 13.2: entrar com nome → cookie → "já estou dentro?"
    bia = env.lan("192.168.1.50")
    r = bia.post("/api/guest/session", json={"token": token, "name": "Bia"})
    assert r.status_code == 201, r.text
    me = r.json()["guest"]
    assert me["name"] == "Bia" and "pending_items" not in me["limits"]
    assert bia.get("/api/guest/session").json()["guest"]["id"] == me["id"]
    caio = env.lan("192.168.1.51")
    assert caio.post("/api/guest/session", json={"token": token, "name": "Caio"}).status_code == 201
    caio_id = caio.get("/api/guest/session").json()["guest"]["id"]
    assert host.get("/api/guest/state").json()["sessions"] == 2

    bia_p = Principal(role="guest", id=me["id"], name="Bia")
    host_sub = subscribe(env, "control", HOST)
    bia_sub = subscribe(env, "guest", bia_p)

    # AC-14.1: biblioteca (Prontas) e busca do YouTube marcando o que já está pronto
    songs = bia.get("/api/songs", params={"status": "ready", "sort": "title"}).json()["songs"]
    assert {s["id"] for s in songs} == {SONG_A, SONG_B, SONG_C, SONG_D}
    res = bia.get("/api/search", params={"q": "queen", "limit": 15}).json()["results"]
    by_id = {x["youtube_id"]: x for x in res}
    assert by_id[SONG_A]["library"]["status"] == "ready" and by_id[NEW_ID]["library"] is None

    # AC-14.2: pedido "Pronta" → fim da fila, singer editável, added_by/added_by_name da sessão
    r = bia.post("/api/queue", json={"song_id": SONG_A, "singer": "Ana"})
    assert r.status_code == 201, r.text
    bia_item = r.json()
    assert (
        bia_item["added_by"] == me["id"]
        and bia_item["added_by_name"] == "Bia"
        and bia_item["singer"] == "Ana"
    )
    assert bia_item["can_remove"] is True

    # AC-16.1: o Controle (SSE do host) vê "pedido por Bia" e pode remover; o convidado também recebe a fila
    host_q = last(drain(env, host_sub), "queue.updated")
    hi = next(i for i in host_q["items"] if i["id"] == bia_item["id"])
    assert hi["added_by_name"] == "Bia" and hi["can_remove"] is True
    guest_events = drain(env, bia_sub)
    assert last(guest_events, "queue.updated")["items"][0]["added_by_name"] == "Bia"
    assert not {t for t, _ in guest_events} & {"guest.state", "settings.updated", "system.updated"}  # AC-15.3

    # AC-16.2: up_next/PlayerState exposto ao convidado não vaza settings; o hello do host traz guest_state
    host_hello = hello_snapshot(env.app, "c_h", "host", HOST)
    assert host_hello["guest_state"]["sessions"] == 2
    assert host_hello["queue"]["items"][0]["added_by_name"] == "Bia"
    g_hello = hello_snapshot(env.app, "c_g", bia_p.id, bia_p)
    assert set(g_hello) == {"server_time", "client_id", "guest", "player", "queue", "jobs"}

    # AC-15.2: Caio pede; Bia não remove o item do Caio (403) nem vê can_remove nele
    caio_item = caio.post("/api/queue", json={"song_id": SONG_B}).json()
    assert caio_item["singer"] == "Caio" and caio_item["added_by_name"] == "Caio"
    q_bia = bia.get("/api/queue").json()["items"]
    assert [i["can_remove"] for i in q_bia] == [True, False]
    # SSE personalizado: no stream do convidado o item do Caio também chega com can_remove=false
    q_sse = last(drain(env, bia_sub), "queue.updated")["items"]
    assert [i["can_remove"] for i in q_sse] == [True, False]
    r = bia.delete(f"/api/queue/{caio_item['id']}")
    assert r.status_code == 403 and code(r) == "not_owner"

    # o convidado remove o próprio; o host (moderação, DECISION 0006) remove o de outro convidado
    assert bia.delete(f"/api/queue/{bia_item['id']}").status_code == 204
    assert host.delete(f"/api/queue/{caio_item['id']}").status_code == 204
    assert host.get("/api/queue").json()["items"] == []

    # AC-15.3: áudio e rotas host fechados para o convidado
    assert bia.get(f"/media/songs/{SONG_A}/instrumental.opus").status_code == 403
    assert bia.get("/api/settings").status_code == 403
    assert bia.get("/api/jobs").status_code == 403

    # AC-14.4: vídeo longo recusado pelo servidor
    r = bia.post("/api/jobs", json={"youtube_id": LONG_ID, "confirm_long": True})
    assert r.status_code == 409 and code(r) == "long_video_not_allowed"
    assert r.json()["error"]["details"] == {"duration_ms": 1_260_000, "max_ms": 900_000}

    # AC-14.3: música nova → job do convidado (enfileirado como "preparando"); job.updated só para ele
    caio_sub = subscribe(env, "guest", Principal(role="guest", id=caio_id, name="Caio"))
    r = bia.post("/api/jobs", json={"youtube_id": NEW_ID, "singer": "Bia"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["job"]["requested_by"] == me["id"] and body["queue_item"]["ready"] is False
    assert body["queue_item"]["added_by"] == me["id"] and body["song"]["added_by"] == "host"
    assert bia.get(f"/api/jobs/{body['job']['id']}").status_code == 200
    assert caio.get(f"/api/jobs/{body['job']['id']}").status_code == 404
    assert any(t == "job.updated" for t, _ in drain(env, bia_sub))
    assert not any(t.startswith("job.") for t, _ in drain(env, caio_sub))

    # DECISION 0016: sem cota de pendentes (já tem 1 do job) → o 4º pedido também entra
    for s in (SONG_C, SONG_D, SONG_B):
        assert bia.post("/api/queue", json={"song_id": s}).status_code == 201

    # AC-11.3: Novo QR → token velho não cria sessão; sessões vivas continuam; QR muda
    st_r = host.post("/api/guest/rotate").json()
    assert st_r["qr"] != st["qr"] and st_r["sessions"] == 2
    r = env.lan("192.168.1.60").post("/api/guest/session", json={"token": token, "name": "Davi"})
    assert r.status_code == 401 and code(r) == "guest_invite_invalid"
    assert bia.get("/api/queue").status_code == 200
    assert any(t == "guest.state" for t, _ in drain(env, host_sub))

    # AC-11.4: Encerrar convidados → 401 + guest.ended; os pedidos ficam na fila do host
    host.post("/api/guest/end-sessions")
    r = bia.get("/api/queue")
    assert r.status_code == 401 and code(r) == "guest_session_required"
    assert bia.get("/api/guest/session").status_code == 401
    assert len(host.get("/api/queue").json()["items"]) == 4  # sem cota: 4 pedidos de Bia (DECISION 0016)

    # desligar: tudo fecha e a LAN volta a ser recusada
    st_off = host.post("/api/guest/disable").json()
    assert st_off["enabled"] is False and st_off["url"] is None and env.listener.calls[-1] == ("stop",)
    assert env.lan().get("/api/health").status_code == 403


def test_guest_ended_reaches_guest_stream(guest_env):
    """`guest.ended` chega ao stream do convidado antes de ele ser fechado (end-sessions e disable)."""
    env = guest_env
    env.enable()
    g = env.join("Bia")
    gid = g.get("/api/guest/session").json()["guest"]["id"]
    sub = subscribe(env, "guest", Principal(role="guest", id=gid, name="Bia"))
    env.host.post("/api/guest/end-sessions")
    got = drain(env, sub)
    assert ("guest.ended", {"reason": "ended"}) in got

    g2 = env.join("Caio", ip="192.168.1.70")
    gid2 = g2.get("/api/guest/session").json()["guest"]["id"]
    sub2 = subscribe(env, "guest", Principal(role="guest", id=gid2, name="Caio"))
    env.host.post("/api/guest/disable")
    assert ("guest.ended", {"reason": "disabled"}) in drain(env, sub2)


def test_guest_media_thumb_allowed_but_audio_forbidden(guest_env):
    """A capa (`thumb.jpg`) que a página `/guest` usa é liberada; o áudio não (AC-15.3)."""
    seed_song(guest_env.app, SONG_A, files=True)
    write_media(guest_env.app.state.config, SONG_A)
    guest_env.enable()
    g = guest_env.join()
    assert g.get(f"/media/songs/{SONG_A}/thumb.jpg").status_code == 200
    for f in ("instrumental.opus", "vocals.opus"):
        assert g.get(f"/media/songs/{SONG_A}/{f}").status_code == 403
