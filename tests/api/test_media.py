"""Mídia com Range/ETag/immutable — contrato §2."""

from __future__ import annotations

from tests.api.core_helpers import SONG_A, SONG_B, seed_song


def test_full_file_headers(client, app):
    seed_song(app, SONG_A, files=True)
    r = client.get(f"/media/songs/{SONG_A}/instrumental.opus?v=1")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/ogg"
    assert r.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert r.headers["accept-ranges"] == "bytes"
    assert "etag" in r.headers
    assert r.content == bytes(range(256)) * 40
    assert client.get(f"/media/songs/{SONG_A}/vocals.opus").headers["content-type"] == "audio/ogg"
    assert client.get(f"/media/songs/{SONG_A}/thumb.jpg").headers["content-type"] == "image/jpeg"


def test_range_request(client, app):
    seed_song(app, SONG_A, files=True)
    r = client.get(f"/media/songs/{SONG_A}/instrumental.opus", headers={"Range": "bytes=256-511"})
    assert r.status_code == 206
    assert r.headers["content-range"] == "bytes 256-511/10240"
    assert r.content == bytes(range(256))
    r = client.get(f"/media/songs/{SONG_A}/instrumental.opus", headers={"Range": "bytes=99999-"})
    assert r.status_code == 416


def test_etag_revalidation(client, app):
    seed_song(app, SONG_A, files=True)
    etag = client.get(f"/media/songs/{SONG_A}/vocals.opus").headers["etag"]
    r = client.get(f"/media/songs/{SONG_A}/vocals.opus", headers={"If-None-Match": etag})
    assert r.status_code == 304


def test_missing_and_invalid(client, app):
    seed_song(app, SONG_B)  # sem arquivos
    r = client.get(f"/media/songs/{SONG_B}/instrumental.opus")
    assert r.status_code == 404 and r.json()["error"]["code"] == "song_not_found"
    assert client.get("/media/songs/..%2F..%2Fx/thumb.jpg").status_code in (400, 404)
    assert client.get("/media/songs/abc/thumb.jpg").status_code == 400
