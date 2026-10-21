"""Cadeia de busca (ADR-0005 §3/§4): disparo, parada, exaustiva, dedupe. Provedores falsos; conteúdo fictício."""

from __future__ import annotations

import pytest

from karaoke import lrclib
from karaoke.lyrics_providers import PlainLyrics, ProviderNetworkError
from karaoke.lyrics_providers.chain import fingerprint, match, plain_candidate, search_lyrics

ART, MUS = "Banda Teste", "Canção de Exemplo"
TEXT = ["Linha um inventada", "Linha dois inventada", "Linha três inventada"]


class FakeLrclib:
    def __init__(self, records=None, error=None):
        self.records, self.error = records or [], error

    def get(self, *a, **k):
        if self.error:
            raise self.error
        return None

    def search(self, *a, **k):
        if self.error:
            raise self.error
        return list(self.records)


class FakeProvider:
    def __init__(self, name, result=None, error=None):
        self.name, self.result, self.error, self.calls = name, result or [], error, 0

    def search(self, track, artist):
        self.calls += 1
        if self.error:
            raise self.error
        return list(self.result)

    def close(self):
        pass


def plain(provider="vagalume", pid="vagalume:banda-teste/cancao-de-exemplo", lines=TEXT):
    return PlainLyrics(provider, pid, MUS, ART, None, list(lines))


def rec(id_, dur_s, synced=True):
    return lrclib.LrcRecord(
        id=id_,
        track_name=MUS,
        artist_name=ART,
        album_name=None,
        duration_ms=int(dur_s * 1000),
        synced_lyrics="[00:01.00]Registro sincronizado inventado" if synced else None,
        plain_lyrics=None if synced else "Texto puro inventado do registro",
    )


def run(client, providers, exhaustive=False, **kw):
    return search_lyrics(client, providers, MUS, ART, 200_000, None, exhaustive=exhaustive, **kw)


def test_match():
    assert match("Canção de Exemplo (Ao Vivo)", "Banda Teste", MUS, ART)
    assert match(MUS, "Banda Teste e Convidados", MUS, ART)
    assert not match(MUS, "Banda Testemunha", MUS, ART)  # palavras inteiras
    assert not match("Outra Canção", ART, MUS, ART)
    assert match(MUS, "Qualquer", MUS, None)


@pytest.mark.parametrize("synced", [True, False])
def test_lrclib_acceptable_skips_extras(synced):  # AC-22.2
    v, o = FakeProvider("vagalume", [plain()]), FakeProvider("lyricsovh")
    res = run(FakeLrclib([rec(1, 201, synced)]), [v, o])
    assert res.status == {"lrclib": "ok", "vagalume": "skipped", "lyricsovh": "skipped"}
    assert v.calls == o.calls == 0 and res.extra == []


def test_lrclib_wrong_duration_triggers_extras_and_stops_at_first():
    v = FakeProvider("vagalume", [plain()])
    o = FakeProvider("lyricsovh", [plain("lyricsovh", "lyricsovh:1", ["Outra letra inventada"])])
    res = run(FakeLrclib([rec(1, 260)]), [v, o])
    assert res.status == {"lrclib": "ok", "vagalume": "ok", "lyricsovh": "skipped"}
    assert [p.provider for p in res.extra] == ["vagalume"] and o.calls == 0


def test_lrclib_offline_skips_extras():
    v = FakeProvider("vagalume", [plain()])
    res = run(FakeLrclib(error=lrclib.LrclibNetworkError("x")), [v])
    assert res.status == {"lrclib": "offline", "vagalume": "skipped"} and v.calls == 0


def test_lrclib_down_still_tries_extras():
    res = run(FakeLrclib(error=lrclib.LrclibUnavailableError("503")), [FakeProvider("vagalume", [plain()])])
    assert res.status == {"lrclib": "timeout", "vagalume": "ok"} and len(res.extra) == 1


def test_provider_offline_skips_following_timeout_continues():
    o = FakeProvider("lyricsovh", [plain("lyricsovh", "lyricsovh:1")])
    res = run(FakeLrclib(), [FakeProvider("vagalume", error=ProviderNetworkError("offline")), o])
    assert res.status["vagalume"] == "offline" and res.status["lyricsovh"] == "skipped"
    res = run(FakeLrclib(), [FakeProvider("vagalume", error=ProviderNetworkError("timeout")), o])
    assert res.status == {"lrclib": "empty", "vagalume": "timeout", "lyricsovh": "ok"}


def test_unexpected_exception_is_error_not_raise():  # AC-22.4
    res = run(FakeLrclib(), [FakeProvider("vagalume", error=RuntimeError("bug"))])
    assert res.status["vagalume"] == "error" and res.extra == []


def test_exhaustive_queries_all_independently():  # AC-23.1
    v = FakeProvider("vagalume", [plain()])
    o = FakeProvider("lyricsovh", [plain("lyricsovh", "lyricsovh:1", ["Outra letra inventada"])])
    res = run(FakeLrclib([rec(1, 201)]), [v, o], exhaustive=True)
    assert res.status == {"lrclib": "ok", "vagalume": "ok", "lyricsovh": "ok"}
    assert [p.provider for p in res.extra] == ["vagalume", "lyricsovh"]
    res = run(FakeLrclib(error=lrclib.LrclibNetworkError("x")), [v, o], exhaustive=True)
    assert res.status["lrclib"] == "offline" and len(res.extra) == 2
    assert not res.all_network_failed()


def test_all_network_failed():
    res = run(
        FakeLrclib(error=lrclib.LrclibNetworkError("x")),
        [FakeProvider("vagalume", error=ProviderNetworkError("timeout"))],
        exhaustive=True,
    )
    assert res.all_network_failed()


def test_dedupe_by_provider_id_and_text():
    same_text = plain("lyricsovh", "lyricsovh:1")  # mesmo começo de texto que o do Vagalume
    res = run(
        FakeLrclib(), [FakeProvider("vagalume", [plain()]), FakeProvider("lyricsovh", [same_text])], True
    )
    assert [p.provider for p in res.extra] == ["vagalume"] and res.status["lyricsovh"] == "ok"
    res = run(FakeLrclib(), [FakeProvider("vagalume", [plain()])], True, known={plain().provider_id})
    assert res.extra == [] and res.status["vagalume"] == "ok"
    res = run(FakeLrclib(), [FakeProvider("vagalume", [plain()])], True, known_prints={fingerprint(TEXT)})
    assert res.extra == []
    lr = rec(5, 260, synced=False)
    lr_text = plain(lines=["Texto puro inventado do registro"])
    assert run(FakeLrclib([lr]), [FakeProvider("vagalume", [lr_text])], True).extra == []


def test_plain_candidate_shape():
    c = plain_candidate(plain())
    assert c["source"] == "online_plain" and c["timing"] == "none" and c["lines"] == []
    assert c["text_lines"] == TEXT and c["meta"]["provider"] == "vagalume" and c["meta"]["album_name"] is None
