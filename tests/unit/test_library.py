"""Serialização de Song, normalização de busca e `can_remove` por ator no EventBus."""

from __future__ import annotations

import asyncio
import sqlite3

from karaoke import db as kdb
from karaoke.events import EventBus, personalize
from karaoke.security import HOST, Principal
from karaoke.services.library import load_song, normalize, song_to_dict

GUEST = Principal(role="guest", id="guest:x")


def _conn() -> sqlite3.Connection:
    conn = kdb.connect(":memory:")
    kdb.migrate(conn)
    conn.execute(
        "INSERT INTO songs(id, title, artist, status, added_by, media_version, created_at, updated_at) "
        "VALUES ('fJ9rUzIMcZQ', 'Canção', 'Ásia', 'ready', 'guest:x', 2, 't', 't')"
    )
    return conn


def test_normalize():
    assert normalize("  Canção ÁSIA ") == "cancao asia"
    assert normalize(None) == ""


def test_song_to_dict_plain_row_and_owner():
    conn = _conn()
    row = conn.execute("SELECT * FROM songs").fetchone()
    song = song_to_dict(row, "host", conn)
    assert song["media"]["version"] == 2 and song["media"]["vocals"].endswith("?v=2")
    assert song["added_by"] == "guest:x"
    assert song["can_remove"] is False  # host não remove música do guest (DECISION 0012)
    assert song_to_dict(row, "guest:x", conn)["can_remove"] is True
    assert load_song(conn, "dQw4w9WgXcQ") is None


def test_personalize_recomputes_can_remove_deeply():
    payload = {
        "current": {
            "added_by": "host",
            "can_remove": False,
            "song": {"added_by": "guest:x", "can_remove": True},
        },
        "items": [{"added_by": "guest:x", "can_remove": False}],
        "other": {"added_by": "host"},  # sem can_remove: intocado
    }
    host = personalize(payload, "host")
    assert host["current"]["can_remove"] is True and host["current"]["song"]["can_remove"] is False
    assert host["items"][0]["can_remove"] is False and "can_remove" not in host["other"]
    guest = personalize(payload, "guest:x")
    assert guest["current"]["can_remove"] is False and guest["items"][0]["can_remove"] is True
    assert payload["items"][0]["can_remove"] is False  # original não é alterado


def test_bus_delivers_can_remove_per_subscriber_actor():
    async def run():
        bus = EventBus()
        bus.bind_loop()
        s_host = bus.subscribe("c_h", "control", HOST)
        s_guest = bus.subscribe("c_g", "control", GUEST)
        seen = []
        bus.add_listener(lambda ev: seen.append(ev.type))
        bus.publish("queue.updated", {"items": [{"added_by": "guest:x", "can_remove": True}]})
        e_host = await asyncio.wait_for(s_host.queue.get(), 1)
        e_guest = await asyncio.wait_for(s_guest.queue.get(), 1)
        assert e_host.id == e_guest.id
        assert e_host.data["items"][0]["can_remove"] is False
        assert e_guest.data["items"][0]["can_remove"] is True
        assert seen == ["queue.updated"]
        assert bus.connected("c_g", "control") and not bus.connected("c_g", "stage")

    asyncio.run(run())
