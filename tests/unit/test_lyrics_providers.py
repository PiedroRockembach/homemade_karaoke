"""Provedores extras de letra (ADR-0005 §1.1) com httpx.MockTransport — zero rede real. Conteúdo fictício."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from karaoke.lyrics_providers import ProviderNetworkError, build_providers, clean_text
from karaoke.lyrics_providers.lyricsovh import LyricsOvhProvider
from karaoke.lyrics_providers.vagalume import VagalumeProvider, parse_page, slug

FIX = Path(__file__).parent.parent / "fixtures" / "lyrics_providers"
ART, MUS = "Banda Teste", "Canção de Exemplo"
PAGE_PATH = "/banda-teste/cancao-de-exemplo.html"


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


class Routes:
    """Transporte falso: (host, path) → resposta ou exceção; registra as chamadas."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        r = self.routes.get((req.url.host, req.url.path))
        if r is None:
            return httpx.Response(404, text="not found")
        if isinstance(r, Exception):
            raise r
        status, body = r
        return httpx.Response(status, text=body)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def test_slug():
    assert slug("Banda Ação Teste") == "banda-acao-teste"
    assert slug("Banda & Cia.") == "banda-e-cia"
    assert slug("") == ""


def test_parse_page_fixture():
    track, artist, lines = parse_page(fx("vagalume_page.html"))
    assert (track, artist) == (MUS, ART)
    assert lines == [
        "Linha um da canção de teste",
        "Segunda linha inventada",
        "Terceira linha de exemplo",
        "Quarta linha fictícia",
        "Quinta linha & final",
    ]  # <br> → linhas, entidades convertidas, [Refrão] removido


def test_parse_page_og_title_fallback():
    html = fx("vagalume_page.html").replace("<h1>Can&ccedil;&atilde;o de Exemplo</h1>", "")
    html = html.replace('<h1 class="darkBG long">', "<h2>").replace("</a></h1>", "</a></h2>")
    track, artist, _ = parse_page(html)
    assert (track, artist) == (MUS, ART)


def test_vagalume_page_success():
    r = Routes({("www.vagalume.com.br", PAGE_PATH): (200, fx("vagalume_page.html"))})
    out = VagalumeProvider(None, transport=r.transport()).search(MUS, ART)
    assert len(out) == 1 and len(r.calls) == 1
    p = out[0]
    assert p.provider == "vagalume" and p.provider_id == "vagalume:banda-teste/cancao-de-exemplo"
    assert p.track_name == MUS and p.artist_name == ART and p.duration_ms is None
    assert p.text_lines[0] == "Linha um da canção de teste"


def test_vagalume_page_404_and_html_changed():
    assert VagalumeProvider(None, transport=Routes({}).transport()).search(MUS, ART) == []
    r = Routes({("www.vagalume.com.br", PAGE_PATH): (200, fx("vagalume_page_changed.html"))})
    assert VagalumeProvider(None, transport=r.transport()).search(MUS, ART) == []


def test_vagalume_without_artist_makes_no_request():
    r = Routes({})
    assert VagalumeProvider(None, transport=r.transport()).search(MUS, None) == []
    assert r.calls == []


def test_vagalume_name_mismatch_discarded():  # AC-22.3
    r = Routes({("www.vagalume.com.br", "/banda-teste/outra-faixa.html"): (200, fx("vagalume_page.html"))})
    assert VagalumeProvider(None, transport=r.transport()).search("Outra Faixa", ART) == []


def test_vagalume_api_with_key():
    r = Routes({("api.vagalume.com.br", "/search.php"): (200, fx("vagalume_api_exact.json"))})
    out = VagalumeProvider("segredo123", transport=r.transport()).search(MUS, ART)
    assert len(r.calls) == 1 and r.calls[0].url.params["apikey"] == "segredo123"
    assert out[0].provider_id == "vagalume:api:m9x8y7" and out[0].text_lines[1] == "Segunda linha inventada"


@pytest.mark.parametrize(
    "api", [(403, "{}"), (503, "down"), (200, fx("vagalume_api_notfound.json")), (200, "not json")]
)
def test_vagalume_api_failure_falls_back_to_page(api):
    r = Routes(
        {
            ("api.vagalume.com.br", "/search.php"): api,
            ("www.vagalume.com.br", PAGE_PATH): (200, fx("vagalume_page.html")),
        }
    )
    out = VagalumeProvider("k", transport=r.transport()).search(MUS, ART)
    assert [c.url.host for c in r.calls] == ["api.vagalume.com.br", "www.vagalume.com.br"]
    assert out and out[0].provider_id == "vagalume:banda-teste/cancao-de-exemplo"


def test_vagalume_offline_no_second_request():
    r = Routes({("api.vagalume.com.br", "/search.php"): httpx.ConnectError("boom")})
    with pytest.raises(ProviderNetworkError) as ei:
        VagalumeProvider("k", transport=r.transport()).search(MUS, ART)
    assert ei.value.kind == "offline" and len(r.calls) == 1


def test_vagalume_timeout():
    r = Routes({("www.vagalume.com.br", PAGE_PATH): httpx.ReadTimeout("slow")})
    with pytest.raises(ProviderNetworkError) as ei:
        VagalumeProvider(None, transport=r.transport()).search(MUS, ART)
    assert ei.value.kind == "timeout" and len(r.calls) == 1


OVH = "api.lyrics.ovh"


def ovh_routes(lyrics=(200, None)) -> Routes:
    return Routes(
        {
            (OVH, "/suggest/Grupo Ficticio Melodia Azul"): (200, fx("lyricsovh_suggest.json")),
            (OVH, "/v1/Grupo Ficticio/Melodia Azul"): (
                lyrics if lyrics[1] is not None else (200, fx("lyricsovh_lyrics.json"))
            ),
        }
    )


def test_lyricsovh_success_and_cleanup():
    r = ovh_routes()
    out = LyricsOvhProvider(transport=r.transport()).search("Melodia Azul", "Grupo Ficticio")
    assert len(r.calls) == 2
    p = out[0]
    assert p.provider == "lyricsovh" and p.provider_id == "lyricsovh:424242" and p.duration_ms == 266000
    assert p.text_lines == [  # sem "Paroles de la chanson…" e sem [marcadores]
        "Primeira linha azul inventada",
        "Segunda linha azul de teste",
        "Terceira linha azul neutra",
        "Quarta linha azul final",
    ]


def test_lyricsovh_404():
    r = ovh_routes(lyrics=(404, fx("lyricsovh_404.json")))
    assert LyricsOvhProvider(transport=r.transport()).search("Melodia Azul", "Grupo Ficticio") == []


def test_lyricsovh_name_mismatch():  # AC-22.3: nenhuma sugestão com o mesmo nome → nada, sem 2ª requisição
    r = Routes({(OVH, "/suggest/Grupo Ficticio Outra"): (200, fx("lyricsovh_suggest.json"))})
    assert LyricsOvhProvider(transport=r.transport()).search("Outra", "Grupo Ficticio") == []
    assert len(r.calls) == 1


def test_lyricsovh_offline():
    r = Routes({(OVH, "/suggest/Grupo Ficticio Melodia Azul"): httpx.ConnectError("x")})
    with pytest.raises(ProviderNetworkError) as ei:
        LyricsOvhProvider(transport=r.transport()).search("Melodia Azul", "Grupo Ficticio")
    assert ei.value.kind == "offline" and len(r.calls) == 1


def test_clean_text():
    assert clean_text("[Intro]\nA linha\n\n[Parte 2]\nB linha [x]") == ["A linha", "B linha [x]"]


def test_build_providers():
    assert build_providers({"extra_lyrics_sources": False}) == []
    ps = build_providers({"extra_lyrics_sources": True, "vagalume_api_key": None})
    try:
        assert [p.name for p in ps] == ["vagalume", "lyricsovh"]
    finally:
        for p in ps:
            p.close()


def test_suggest_json_shape():
    assert json.loads(fx("lyricsovh_suggest.json"))["data"][1]["artist"]["name"] == "Grupo Ficticio"


# ---- FIX-001 (review-1) -------------------------------------------------------------------------------------
def test_slug_apostrophe_and_noise():
    assert slug("Don't Pare Agora") == "dont-pare-agora"
    assert slug("Gota d’Água Fictícia") == "gota-dagua-ficticia"


def test_vagalume_page_uses_clean_title_for_slug():
    r = Routes({("www.vagalume.com.br", PAGE_PATH): (200, fx("vagalume_page.html"))})
    out = VagalumeProvider(None, transport=r.transport()).search(f"{MUS} (Ao Vivo)", ART)
    assert len(out) == 1 and r.calls[0].url.path == PAGE_PATH


def test_parse_page_ignores_script_and_style_in_lyrics():
    html = fx("vagalume_page.html").replace(
        "Segunda linha inventada<br>",
        "Segunda linha inventada<br><script>var anuncio = 1;</script><style>.x{color:red}</style>",
    )
    _, _, lines = parse_page(html)
    assert not any("anuncio" in ln or "color" in ln for ln in lines) and len(lines) == 5


def test_ids_never_none():
    api = json.loads(fx("vagalume_api_exact.json"))
    del api["mus"][0]["id"]
    r = Routes({("api.vagalume.com.br", "/search.php"): (200, json.dumps(api))})
    out = VagalumeProvider("k", transport=r.transport()).search(MUS, ART)
    assert out[0].provider_id == "vagalume:banda-teste/cancao-de-exemplo"
    sug = json.loads(fx("lyricsovh_suggest.json"))
    del sug["data"][1]["id"]
    r = ovh_routes()
    r.routes[(OVH, "/suggest/Grupo Ficticio Melodia Azul")] = (200, json.dumps(sug))
    out = LyricsOvhProvider(transport=r.transport()).search("Melodia Azul", "Grupo Ficticio")
    assert out[0].provider_id == "lyricsovh:grupo-ficticio/melodia-azul"


def test_build_providers_closes_first_if_second_fails(monkeypatch):
    import karaoke.lyrics_providers.lyricsovh as ovh
    import karaoke.lyrics_providers.vagalume as vg

    closed = []
    monkeypatch.setattr(vg.VagalumeProvider, "close", lambda self: closed.append("vagalume"))

    def boom(*a, **k):
        raise RuntimeError("falhou")

    monkeypatch.setattr(ovh.LyricsOvhProvider, "__init__", boom)
    with pytest.raises(RuntimeError):
        build_providers({"extra_lyrics_sources": True})
    assert closed == ["vagalume"]
