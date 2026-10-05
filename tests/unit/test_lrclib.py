import httpx
import pytest

from karaoke import lrclib
from tests.fixtures.pipeline import lrclib_record, lrclib_transport


def client(**kw):
    return lrclib.LrclibClient(transport=lrclib_transport(**kw))


def test_get_and_search_ranked_by_duration():
    t = lrclib_transport(
        get=lrclib_record(1, 361),
        search=[
            lrclib_record(2, 358),
            lrclib_record(3, 300),
            {"id": 4, "trackName": "x", "syncedLyrics": None},
        ],
    )
    recs = lrclib.find_candidates(lrclib.LrclibClient(transport=t), "Bohemian Rhapsody", "Queen", 359000)
    assert [r.id for r in recs] == [2, 1, 3]  # |−1 s| < |+2 s| < |−59 s|; sem syncedLyrics fica de fora
    assert lrclib.acceptable(recs[0], 359000)
    assert "duration=359" in t.calls[0] and "/api/get" in t.calls[0]


def test_not_acceptable_beyond_3s():
    recs = lrclib.find_candidates(client(search=[lrclib_record(9, 365)]), "B", "Queen", 359000)
    assert recs and not lrclib.acceptable(recs[0], 359000)
    assert not lrclib.acceptable(None, 359000)


def test_free_text_fallback_when_nothing_close():
    t = lrclib_transport(search=[])
    lrclib.find_candidates(lrclib.LrclibClient(transport=t), "Song", "Artist", 200000)
    assert any("q=Artist" in c for c in t.calls)


def test_network_error():
    with pytest.raises(lrclib.LrclibNetworkError):
        lrclib.find_candidates(client(fail=True), "B", "Queen", 1000)


def test_dedup_and_instrumental_skipped():
    a = lrclib_record(1, 100)
    inst = dict(lrclib_record(2, 100), instrumental=True)
    recs = lrclib.rank(
        [lrclib.LrcRecord.from_api(x) for x in (a, a, inst)], 100000, "Bohemian Rhapsody", "Queen"
    )
    assert [r.id for r in recs] == [1]


# ---- QA M-04: 429/5xx/timeout transitórios têm novas tentativas e erro próprio --------------------------------
def _flaky(statuses, search=None):
    """Transport que responde os `statuses` em sequência (int = status; Exception = levanta) e depois 200."""
    calls: list[str] = []
    seq = list(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if seq:
            nxt = seq.pop(0)
            if isinstance(nxt, type) and issubclass(nxt, Exception):
                raise nxt("falha simulada", request=request)
            return httpx.Response(nxt, headers={"retry-after": "1"} if nxt == 429 else {})
        return httpx.Response(200, json=search or [lrclib_record(5, 200)])

    t = httpx.MockTransport(handler)
    t.calls = calls  # type: ignore[attr-defined]
    return t


def test_retry_after_transient_503_then_ok():
    sleeps: list[float] = []
    t = _flaky([503])
    recs = lrclib.LrclibClient(transport=t, sleep=sleeps.append).search("Song", "Artist")
    assert [r.id for r in recs] == [5]
    assert len(t.calls) == 2 and sleeps == [lrclib.RETRY_BACKOFF_S[0]]


def test_429_honors_retry_after_capped():
    sleeps: list[float] = []
    lrclib.LrclibClient(transport=_flaky([429]), sleep=sleeps.append).search("Song", "Artist")
    assert sleeps == [max(lrclib.RETRY_BACKOFF_S[0], 1.0)]


def test_persistent_5xx_raises_unavailable_after_retries():
    sleeps: list[float] = []
    t = _flaky([503, 502, 500, 503])
    with pytest.raises(lrclib.LrclibUnavailableError):
        lrclib.LrclibClient(transport=t, sleep=sleeps.append).search("Song", "Artist")
    assert len(t.calls) == 1 + len(lrclib.RETRY_BACKOFF_S)
    assert sleeps == list(lrclib.RETRY_BACKOFF_S)
    # continua sendo um LrclibNetworkError (o pipeline cai no Whisper do mesmo jeito)
    assert issubclass(lrclib.LrclibUnavailableError, lrclib.LrclibNetworkError)


def test_timeout_retried_once_then_unavailable():
    sleeps: list[float] = []
    t = _flaky([httpx.ReadTimeout, httpx.ReadTimeout, httpx.ReadTimeout])
    with pytest.raises(lrclib.LrclibUnavailableError):
        lrclib.LrclibClient(transport=t, sleep=sleeps.append).search("Song", "Artist")
    assert len(t.calls) == lrclib.MAX_TIMEOUT_ATTEMPTS


def test_connect_error_is_not_retried():
    sleeps: list[float] = []
    t = _flaky([httpx.ConnectError])
    with pytest.raises(lrclib.LrclibNetworkError) as exc:
        lrclib.LrclibClient(transport=t, sleep=sleeps.append).search("Song", "Artist")
    assert not isinstance(exc.value, lrclib.LrclibUnavailableError)
    assert len(t.calls) == 1 and sleeps == []


# ---- CR-001: registros só com texto e cadeia de buscas --------------------------------------------------------
def plain_record(id_, duration_s, text="Is this the real life\nIs this just fantasy"):
    return dict(lrclib_record(id_, duration_s), syncedLyrics=None, plainLyrics=text)


def test_plain_only_records_are_kept_after_synced():
    recs = lrclib.find_candidates(
        client(search=[plain_record(1, 359), lrclib_record(2, 360), {"id": 3, "syncedLyrics": None}]),
        "Bohemian Rhapsody",
        "Queen",
        359000,
    )
    assert [r.id for r in recs] == [2, 1]  # com tempos primeiro; sem texto nenhum fica de fora
    assert not recs[1].synced and recs[1].plain_lyrics.startswith("Is this")


def _seq_transport(responses):
    """Responde /api/search conforme o parâmetro usado: 'fields' (track_name) ou o texto de `q`."""
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/api/get":
            return httpx.Response(404)
        q = request.url.params.get("q")
        return httpx.Response(200, json=responses.get(q or "fields", []))

    t = httpx.MockTransport(handler)
    t.calls = calls
    return t


def test_search_chain_stops_at_first_good_result():
    t = _seq_transport({"fields": [], "Queen Bohemian Rhapsody": [lrclib_record(7, 359)]})
    recs = lrclib.find_candidates(
        lrclib.LrclibClient(transport=t),
        "Bohemian Rhapsody",
        "Queen",
        359000,
        "Queen - Bohemian Rhapsody (Live Aid 1985)",
    )
    assert [r.id for r in recs] == [7]
    assert len(t.calls) == 3  # get, search por campos, q=artista+faixa (não precisou do título do vídeo)


def test_search_chain_uses_cleaned_video_title_and_only_plain_is_not_good():
    t = _seq_transport(
        {
            "fields": [plain_record(1, 359)],
            "Queen Bohemian Rhapsody": [],
            "Queen Bohemian Rhapsody Remastered 2011": [lrclib_record(9, 360)],
        }
    )
    recs = lrclib.find_candidates(
        lrclib.LrclibClient(transport=t),
        "Bohemian Rhapsody",
        "Queen",
        359000,
        "Queen Bohemian Rhapsody Remastered 2011 (Official Video)",
    )
    assert [r.id for r in recs] == [9, 1]
    assert any("Remastered" in c for c in t.calls)
