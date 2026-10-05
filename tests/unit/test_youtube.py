import httpx
import pytest

from karaoke import youtube as yt


@pytest.mark.parametrize(
    ("msg", "code"),
    [
        (
            "ERROR: Unable to download webpage: <urlopen error [Errno -3] Temporary failure in name resolution>",
            "network_unavailable",
        ),
        ("ERROR: [youtube] abc: Video unavailable", "video_unavailable"),
        ("ERROR: [youtube] abc: Private video", "video_unavailable"),
        ("ERROR: [youtube] zzzzzzzzzz0: This video is unavailable", "video_unavailable"),  # QA L-01
        ("ERROR: nsig extraction failed", "youtube_error"),
    ],
)
def test_classify_error(msg, code):
    e = yt.classify_error(Exception(msg))
    assert e.code == code and e.message
    if code == "youtube_error":
        assert "make update-ytdlp" in e.hint


def test_entry_to_result():
    r = yt._entry_to_result({"id": "fJ9rUzIMcZQ", "title": "T", "channel": "C", "duration": 359})
    assert (r.youtube_id, r.duration_ms, r.is_live) == ("fJ9rUzIMcZQ", 359000, False)
    assert r.url.endswith("fJ9rUzIMcZQ")
    live = yt._entry_to_result(
        {"id": "fJ9rUzIMcZQ", "title": "L", "live_status": "is_live", "duration": None}
    )
    assert live.is_live
    assert yt._entry_to_result({"id": "UCxxxxxxxxxxxxxxxxxx", "title": "canal"}) is None


def test_search_uses_flat_extraction(monkeypatch):
    seen = {}

    class FakeYDL:
        def __init__(self, opts):
            seen["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            seen["url"], seen["download"] = url, download
            return {
                "entries": [{"id": "fJ9rUzIMcZQ", "title": "a", "duration": 10}, {"id": "bad", "title": "x"}]
            }

    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    res = yt.search("queen", 15)
    assert [r.youtube_id for r in res] == ["fJ9rUzIMcZQ"]
    assert seen["url"] == "ytsearch15:queen" and seen["download"] is False
    assert seen["opts"]["extract_flat"] == "in_playlist"


def test_search_error_classified(monkeypatch):
    import yt_dlp

    class Boom:
        def __init__(self, opts):
            pass

        def __enter__(self):
            raise yt_dlp.utils.DownloadError("Unable to download webpage: timed out")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Boom)
    with pytest.raises(yt.YoutubeError) as ei:
        yt.search("q")
    assert ei.value.code == "network_unavailable"


def test_thumb_cache(tmp_path):
    hits = []

    def handler(req):
        hits.append(req.url.path)
        if "mqdefault" in req.url.path:
            return httpx.Response(200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"})
        return httpx.Response(404)

    cache = yt.ThumbCache(tmp_path, transport=httpx.MockTransport(handler))
    p = cache.get("fJ9rUzIMcZQ")
    assert p and p.read_bytes().startswith(b"\xff\xd8")
    assert cache.get("fJ9rUzIMcZQ") == p and len(hits) == 1  # segunda vez vem do cache
    assert cache.get("../../etc/pa") is None


def test_thumb_cache_unavailable_and_prune(tmp_path):
    cache = yt.ThumbCache(
        tmp_path, max_bytes=10, transport=httpx.MockTransport(lambda r: httpx.Response(404))
    )
    assert cache.get("fJ9rUzIMcZQ") is None
    for i in range(3):
        (tmp_path / f"aaaaaaaaaa{i}.jpg").write_bytes(b"x" * 8)
    cache.prune()
    assert sum(f.stat().st_size for f in tmp_path.glob("*.jpg")) <= 10


def test_thumb_too_big_is_rejected_and_no_tmp_left(tmp_path):
    big = b"\xff\xd8" + b"x" * (yt.THUMB_MAX_BYTES + 10)
    t = httpx.MockTransport(
        lambda r: httpx.Response(200, content=big, headers={"content-type": "image/jpeg"})
    )
    assert yt.ThumbCache(tmp_path, transport=t).get("fJ9rUzIMcZQ") is None
    assert list(tmp_path.iterdir()) == []


def test_thumb_write_uses_unique_tmp(tmp_path):
    dest = tmp_path / "x.jpg"
    yt._write_atomic(dest, b"a")
    yt._write_atomic(dest, b"b")
    assert dest.read_bytes() == b"b" and [p.name for p in tmp_path.iterdir()] == ["x.jpg"]
