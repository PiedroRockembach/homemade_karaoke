"""Migração v4 (T-004, ADR-0003 §6): `queue_items.added_by_name` sobre um banco v3 real, sem perder dados."""

from __future__ import annotations

from karaoke import db

SID = "fJ9rUzIMcZQ"


def _make_v3(path):
    conn = db.connect(path)
    for version in range(3):
        for stmt in db._split_sql(db.MIGRATIONS[version]):
            conn.execute(stmt)
    conn.execute("PRAGMA user_version = 3")
    now = db.utcnow()
    conn.execute("INSERT INTO songs(id,title,created_at,updated_at) VALUES (?, 't', ?, ?)", (SID, now, now))
    conn.execute(
        "INSERT INTO jobs(id,kind,song_id,stage,requested_by,created_at,updated_at) "
        "VALUES ('j_1','process',?,'done','host',?,?)",
        (SID, now, now),
    )
    conn.execute(
        "INSERT INTO queue_items(id,song_id,singer,sort_key,added_by,added_at) VALUES ('q_1',?,'Ana',1,'host',?)",
        (SID, now),
    )
    conn.execute("INSERT INTO counters(name, value) VALUES ('queue_item', 1)")
    conn.close()


def test_v3_to_v4_keeps_data_and_adds_column(tmp_path):
    path = tmp_path / "v3.db"
    _make_v3(path)
    database = db.Database(path)
    assert database.migrate() == db.SCHEMA_VERSION == 4
    conn = database.connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(queue_items)")}
        assert "added_by_name" in cols
        item = conn.execute("SELECT * FROM queue_items WHERE id='q_1'").fetchone()
        assert (item["singer"], item["added_by"], item["added_by_name"]) == ("Ana", "host", None)
        job = conn.execute("SELECT requested_by FROM jobs WHERE id='j_1'").fetchone()
        assert job["requested_by"] == "host"
        assert conn.execute("SELECT value FROM counters WHERE name='queue_item'").fetchone()[0] == 1
        idx = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {"queue_items_added_by", "jobs_requested_by"} <= idx
        # a coluna nova aceita o nome do convidado
        now = db.utcnow()
        conn.execute(
            "INSERT INTO queue_items(id,song_id,sort_key,added_by,added_by_name,added_at) "
            "VALUES ('q_2',?,2,'g_1','Bia',?)",
            (SID, now),
        )
        assert conn.execute("SELECT added_by_name FROM queue_items WHERE id='q_2'").fetchone()[0] == "Bia"
    finally:
        conn.close()


def test_v4_migration_is_idempotent(tmp_path):
    path = tmp_path / "v3.db"
    _make_v3(path)
    database = db.Database(path)
    database.migrate()
    assert database.migrate() == 4  # rodar de novo não reaplica (nem falha no ALTER)
    conn = database.connect()
    try:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(queue_items)")]
        assert cols.count("added_by_name") == 1
        assert conn.execute("SELECT COUNT(*) FROM queue_items").fetchone()[0] == 1
    finally:
        conn.close()


def test_fresh_database_reaches_v4(tmp_path):
    database = db.Database(tmp_path / "new.db")
    assert database.migrate() == 4
    conn = database.connect()
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(queue_items)")}
        assert "added_by_name" in cols
    finally:
        conn.close()
