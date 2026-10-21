"""ADR-0005 na API: `/lyrics/search` com Vagalume e lyrics.ovh (MockTransport, sem rede; conteúdo fictício)."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest

from karaoke.app import create_app
from karaoke.lyrics_providers import build_providers
from tests.fixtures.pipeline import SONG_ID, insert_ready_song, lrclib_factory, lrclib_transport

FIX = Path(__file__).parent.parent / "fixtures" / "lyrics_providers"
KEY = "chave-secreta-xyz-123"
TERMS = {"track_name": "Canção de Exemplo", "artist_name": "Banda Teste"}
URL = f"/api/songs/{SONG_ID}/lyrics/search"


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def routes_transport(routes: dict, calls: list) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.host)
        r = routes.get(req.url.host)
        if isinstance(r, Exception):
            raise r
        if r is None or (req.url.host == "www.vagalume.com.br" and "banda-teste" not in req.url.path):
            return httpx.Response(404, text="{}")
        if req.url.host == "api.lyrics.ovh" and req.url.path.startswith("/suggest/"):
            return httpx.Response(200, text=fx("lyricsovh_suggest.json"))
        return httpx.Response(200, text=r)

    return httpx.MockTransport(handler)


@pytest.fixture
def app(config):
    a = create_app(config)
    a.state.runner_autostart = False
    a.state.lrclib_transport = lrclib_transport(search=[])
    a.state.lrclib_factory = lambda: lrclib_factory(a.state.lrclib_transport)()
    a.state.provider_calls = []
    a.state.provider_routes = {"www.vagalume.com.br": fx("vagalume_page.html")}
    a.state.lyrics_providers_factory = lambda s: build_providers(
        s, transport=routes_transport(a.state.provider_routes, a.state.provider_calls)
    )
    return a


def test_search_adds_vagalume_with_label_provider_and_sources(client, app):  # AC-23.1
    insert_ready_song(app.state.db, lrc=None)
    r = client.post(URL, json=TERMS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added"] == 1
    assert body["sources"] == {"lrclib": "empty", "vagalume": "ok", "lyricsovh": "empty"}
    c = body["candidates"][0]
    assert (
        c["source"] == "online_plain" and c["provider"] == "vagalume" and c["label"] == "Vagalume · só texto"
    )
    assert c["selectable"] is False and c["official_text"] is True and c["album_name"] is None
    assert c["duration_ms"] is None and c["track_name"] == "Canção de Exemplo" and c["line_count"] == 5
    # de novo: nada duplicado
    again = client.post(URL, json=TERMS).json()
    assert again["added"] == 0 and len(again["candidates"]) == 1


def test_lrclib_candidates_have_provider(client, app):
    from tests.fixtures.pipeline import lrclib_record

    insert_ready_song(app.state.db, lrc=None)
    app.state.lrclib_transport = lrclib_transport(search=[lrclib_record(500, 358)])
    body = client.post(URL, json={}).json()
    assert [(c["source"], c["provider"]) for c in body["candidates"]] == [("lrclib", "lrclib")]


def test_one_source_offline_still_200(client, app):
    insert_ready_song(app.state.db, lrc=None)
    app.state.lrclib_transport = lrclib_transport(fail=True)
    app.state.provider_routes["api.lyrics.ovh"] = httpx.ConnectError("x")
    body = client.post(URL, json=TERMS).json()
    assert body["sources"] == {"lrclib": "offline", "vagalume": "ok", "lyricsovh": "offline"}
    assert body["added"] == 1


def test_502_only_when_all_sources_fail_by_network(client, app):  # AC-23.3
    insert_ready_song(app.state.db, lrc=None)
    app.state.lrclib_transport = lrclib_transport(fail=True)
    app.state.provider_routes = {
        "www.vagalume.com.br": httpx.ReadTimeout("slow"),
        "api.lyrics.ovh": httpx.ConnectError("x"),
    }
    r = client.post(URL, json=TERMS)
    assert r.status_code == 502
    err = r.json()["error"]
    assert err["code"] == "network_unavailable" and err["details"]["reason"] == "all_sources_unavailable"
    assert "LRCLIB, Vagalume e lyrics.ovh" in err["message"]


def test_extra_sources_disabled_keeps_old_behaviour(client, app):  # AC-22.5
    insert_ready_song(app.state.db, lrc=None)
    assert client.patch("/api/settings", json={"extra_lyrics_sources": False}).status_code == 200
    app.state.lrclib_transport = lrclib_transport(fail=True)
    r = client.post(URL, json=TERMS)
    assert r.status_code == 502 and r.json()["error"].get("details") in (None, {})
    assert app.state.provider_calls == []
    app.state.lrclib_transport = lrclib_transport(search=[])
    body = client.post(URL, json=TERMS).json()
    assert body["sources"] == {"lrclib": "empty"} and app.state.provider_calls == []


def test_vagalume_key_never_leaks(client, app, caplog):
    caplog.set_level(logging.DEBUG)
    insert_ready_song(app.state.db, lrc=None)
    r = client.patch("/api/settings", json={"vagalume_api_key": f"  {KEY}  "})
    assert r.status_code == 200 and r.json()["vagalume_api_key_set"] is True and KEY not in r.text
    app.state.provider_routes["api.vagalume.com.br"] = httpx.ConnectError(f"falhou em ?apikey={KEY}")
    r = client.post(URL, json=TERMS)
    assert KEY not in r.text and KEY not in caplog.text
    app.state.provider_routes["api.vagalume.com.br"] = fx("vagalume_api_exact.json")
    r = client.post(URL, json=TERMS)
    assert r.status_code == 200 and KEY not in r.text and KEY not in caplog.text
    assert KEY not in client.get("/api/settings").text


def test_manual_search_without_artist_uses_channel(client, app):  # FIX-001
    insert_ready_song(app.state.db, lrc=None)
    with app.state.db.tx() as conn:
        conn.execute("UPDATE songs SET artist=NULL, channel='Banda Teste - Topic'")
    app.state.lrclib_transport = lrclib_transport(search=[])
    r = client.post(URL, json={"track_name": "Canção de Exemplo"})
    assert r.status_code == 200 and r.json()["sources"]["vagalume"] == "ok"
