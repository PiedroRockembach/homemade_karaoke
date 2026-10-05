from karaoke import db


def test_migrate_creates_schema_and_is_idempotent(tmp_path):
    database = db.Database(tmp_path / "k.db")
    assert database.migrate() == db.SCHEMA_VERSION
    assert database.migrate() == db.SCHEMA_VERSION
    conn = database.connect()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"songs", "lyrics_candidates", "jobs", "queue_items", "player_state", "settings"} <= tables
    for table in ("songs", "queue_items"):
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        assert "added_by" in cols
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_next_id_and_cascade(tmp_path):
    database = db.Database(tmp_path / "k.db")
    database.migrate()
    with database.tx() as conn:
        assert db.next_id(conn, "q") == 1
        assert db.next_id(conn, "q") == 2
        now = db.utcnow()
        conn.execute(
            "INSERT INTO songs(id,title,created_at,updated_at) VALUES ('fJ9rUzIMcZQ','t',?,?)", (now, now)
        )
        conn.execute(
            "INSERT INTO queue_items(id,song_id,sort_key,added_at) VALUES ('q_1','fJ9rUzIMcZQ',1,?)", (now,)
        )
    with database.tx() as conn:
        assert conn.execute("SELECT added_by FROM queue_items").fetchone()[0] == "host"
        conn.execute("DELETE FROM songs")
        assert conn.execute("SELECT COUNT(*) FROM queue_items").fetchone()[0] == 0


def test_migration_v1_to_latest_keeps_data(tmp_path):
    """Banco do usuário na v1 (ciclo anterior) → v2: candidatas e jobs preservados; novas origens/tipos aceitos."""
    path = tmp_path / "old.db"
    conn = db.connect(path)
    for stmt in db._split_sql(db.MIGRATIONS[0]):
        conn.execute(stmt)
    conn.execute("PRAGMA user_version = 1")
    now = db.utcnow()
    conn.execute(
        "INSERT INTO songs(id,title,created_at,updated_at) VALUES ('fJ9rUzIMcZQ','t',?,?)", (now, now)
    )
    conn.execute(
        "INSERT INTO lyrics_candidates(id,song_id,source,timing,meta_json,lyrics_json,created_at) "
        "VALUES ('lrclib:1','fJ9rUzIMcZQ','lrclib','line','{\"provider_id\": 7}','{\"lines\": []}',?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO jobs(id,kind,song_id,stage,options_json,created_at,updated_at) "
        "VALUES ('j_1','process','fJ9rUzIMcZQ','done','{\"language\": \"pt\"}',?,?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO queue_items(id,song_id,sort_key,added_at) VALUES ('q_1','fJ9rUzIMcZQ',1,?)", (now,)
    )
    conn.close()

    database = db.Database(path)
    assert database.migrate() == db.SCHEMA_VERSION == 4
    conn = database.connect()
    cand = conn.execute("SELECT * FROM lyrics_candidates").fetchone()
    assert (
        cand["id"] == "lrclib:1"
        and cand["derived_from"] is None
        and cand["meta_json"] == '{"provider_id": 7}'
    )
    job = conn.execute("SELECT * FROM jobs").fetchone()
    assert job["kind"] == "process" and job["options_json"] == '{"language": "pt"}'
    assert conn.execute("SELECT COUNT(*) FROM queue_items").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    idx = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"lyrics_candidates_song", "jobs_stage", "jobs_song"} <= idx
    conn.execute(
        "INSERT INTO lyrics_candidates(id,song_id,source,timing,lyrics_json,derived_from,created_at) "
        "VALUES ('aligned:1','fJ9rUzIMcZQ','aligned','word','{}','lrclib:1',?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO jobs(id,kind,song_id,created_at,updated_at) VALUES ('j_2','align','fJ9rUzIMcZQ',?,?)",
        (now, now),
    )
    conn.execute("DELETE FROM songs")  # cascade continua valendo nas tabelas reconstruídas
    assert conn.execute("SELECT COUNT(*) FROM lyrics_candidates").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    conn.close()


def test_migration_v2_to_v3_accepts_online_plain(tmp_path):
    path = tmp_path / "v2.db"
    conn = db.connect(path)
    for mig in db.MIGRATIONS[:2]:
        for stmt in db._split_sql(mig):
            conn.execute(stmt)
    conn.execute("PRAGMA user_version = 2")
    now = db.utcnow()
    conn.execute(
        "INSERT INTO songs(id,title,created_at,updated_at) VALUES ('fJ9rUzIMcZQ','t',?,?)", (now, now)
    )
    conn.execute(
        "INSERT INTO lyrics_candidates(id,song_id,source,timing,lyrics_json,derived_from,created_at) "
        "VALUES ('aligned:1','fJ9rUzIMcZQ','aligned','word','{}','lrclib:1',?)",
        (now,),
    )
    conn.close()
    database = db.Database(path)
    assert database.migrate() == db.SCHEMA_VERSION
    conn = database.connect()
    assert conn.execute("SELECT derived_from FROM lyrics_candidates").fetchone()[0] == "lrclib:1"
    conn.execute(
        "INSERT INTO lyrics_candidates(id,song_id,source,timing,lyrics_json,created_at) "
        "VALUES ('online_plain:1','fJ9rUzIMcZQ','online_plain','none','{}',?)",
        (now,),
    )
    conn.close()
