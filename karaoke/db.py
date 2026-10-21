"""SQLite (WAL, foreign_keys=ON) com migrações versionadas por `PRAGMA user_version`. Ver ARCHITECTURE §7.

Uso:
    db = Database(config.db_path); db.migrate()
    with db.tx() as conn:            # transação (commit/rollback automático)
        conn.execute(...)
    conn = db.connect()              # conexão avulsa (feche você mesmo)

Para mudar o schema: **acrescente** uma nova entrada em MIGRATIONS (nunca edite uma já publicada).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS: list[str] = [
    # ---- v1: schema inicial (T-004) -------------------------------------------------
    """
    CREATE TABLE songs (
        id                  TEXT PRIMARY KEY,                 -- ID do vídeo do YouTube (11 chars)
        title               TEXT NOT NULL,
        artist              TEXT,
        original_title      TEXT,
        channel             TEXT,
        duration_ms         INTEGER,
        status              TEXT NOT NULL DEFAULT 'queued'
                            CHECK (status IN ('queued','processing','ready','error')),
        error_code          TEXT,
        lyrics_candidate_id TEXT,                             -- candidata selecionada (sem FK: evita ciclo)
        offset_ms           INTEGER NOT NULL DEFAULT 0,
        size_bytes          INTEGER NOT NULL DEFAULT 0,
        media_version       INTEGER NOT NULL DEFAULT 0,       -- ?v= das URLs de mídia; 0 = sem mídia
        added_by            TEXT NOT NULL DEFAULT 'host',     -- ator que disparou o processamento (regra de posse)
        created_at          TEXT NOT NULL,
        updated_at          TEXT NOT NULL
    );
    CREATE INDEX songs_status ON songs(status);

    CREATE TABLE lyrics_candidates (
        id          TEXT PRIMARY KEY,                         -- lrclib:<n> | whisper:<n> | manual:<n>
        song_id     TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
        source      TEXT NOT NULL CHECK (source IN ('lrclib','whisper','manual')),
        timing      TEXT NOT NULL CHECK (timing IN ('none','line','word')),
        meta_json   TEXT NOT NULL DEFAULT '{}',
        lyrics_json TEXT NOT NULL,
        created_at  TEXT NOT NULL
    );
    CREATE INDEX lyrics_candidates_song ON lyrics_candidates(song_id);

    CREATE TABLE jobs (
        id             TEXT PRIMARY KEY,                      -- j_<n>
        kind           TEXT NOT NULL CHECK (kind IN ('process','transcribe')),
        song_id        TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
        stage          TEXT NOT NULL DEFAULT 'queued'
                       CHECK (stage IN ('queued','model_download','downloading','separating','lyrics',
                                        'finalizing','done','error','canceled')),
        stage_progress REAL,
        progress       REAL NOT NULL DEFAULT 0,
        device         TEXT,
        lyrics_source  TEXT,
        options_json   TEXT NOT NULL DEFAULT '{}',            -- ex.: {"language": "pt"}
        error_code     TEXT,
        error_message  TEXT,
        attempts       INTEGER NOT NULL DEFAULT 0,
        requested_by   TEXT NOT NULL DEFAULT 'host',
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL,
        finished_at    TEXT
    );
    CREATE INDEX jobs_stage ON jobs(stage);
    CREATE INDEX jobs_song ON jobs(song_id);

    CREATE TABLE queue_items (
        id          TEXT PRIMARY KEY,                         -- q_<n>
        song_id     TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
        singer      TEXT,
        status      TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','playing','done','skipped')),
        sort_key    REAL NOT NULL,
        added_by    TEXT NOT NULL DEFAULT 'host',             -- definido pelo servidor (principal), nunca pelo corpo
        added_at    TEXT NOT NULL,
        finished_at TEXT
    );
    CREATE INDEX queue_items_status ON queue_items(status, sort_key);

    CREATE TABLE player_state (
        id         INTEGER PRIMARY KEY CHECK (id = 1),
        revision   INTEGER NOT NULL DEFAULT 0,
        state_json TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT NOT NULL
    );

    CREATE TABLE settings (
        key        TEXT PRIMARY KEY,
        value_json TEXT NOT NULL
    );

    CREATE TABLE counters (                                   -- geradores de id: j_<n>, q_<n>, whisper:<n>...
        name  TEXT PRIMARY KEY,
        value INTEGER NOT NULL
    );

    CREATE TABLE meta (                                       -- revisão da fila etc.
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    INSERT INTO meta(key, value) VALUES ('queue_revision', '0');
    """,
    # ---- v2: letras sincronizadas pela voz (ciclo 20261004-1742, T-001) ---------------------------
    # SQLite não altera CHECK: as duas tabelas são reconstruídas (nenhuma é "pai" de FK, então o DROP é seguro
    # com foreign_keys=ON). lyrics_candidates ganha a origem 'aligned' e `derived_from` (candidata de origem de
    # uma alinhada/manual); jobs ganha os tipos 'align' (sincronizar pela voz) e 'analyze' (detectar offset).
    """
    CREATE TABLE lyrics_candidates_v2 (
        id           TEXT PRIMARY KEY,                        -- lrclib:<n> | whisper:<n> | manual:<n> | aligned:<n>
        song_id      TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
        source       TEXT NOT NULL CHECK (source IN ('lrclib','whisper','manual','aligned')),
        timing       TEXT NOT NULL CHECK (timing IN ('none','line','word')),
        meta_json    TEXT NOT NULL DEFAULT '{}',
        lyrics_json  TEXT NOT NULL,
        derived_from TEXT,                                    -- id da candidata de origem (sem FK: pode ser apagada)
        created_at   TEXT NOT NULL
    );
    INSERT INTO lyrics_candidates_v2(id, song_id, source, timing, meta_json, lyrics_json, derived_from, created_at)
        SELECT id, song_id, source, timing, meta_json, lyrics_json, NULL, created_at FROM lyrics_candidates;
    DROP TABLE lyrics_candidates;
    ALTER TABLE lyrics_candidates_v2 RENAME TO lyrics_candidates;
    CREATE INDEX lyrics_candidates_song ON lyrics_candidates(song_id);

    CREATE TABLE jobs_v2 (
        id             TEXT PRIMARY KEY,
        kind           TEXT NOT NULL CHECK (kind IN ('process','transcribe','align','analyze')),
        song_id        TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
        stage          TEXT NOT NULL DEFAULT 'queued'
                       CHECK (stage IN ('queued','model_download','downloading','separating','lyrics',
                                        'finalizing','done','error','canceled')),
        stage_progress REAL,
        progress       REAL NOT NULL DEFAULT 0,
        device         TEXT,
        lyrics_source  TEXT,
        options_json   TEXT NOT NULL DEFAULT '{}',            -- ex.: {"language": "pt", "lyrics_mode": "aligned"}
        error_code     TEXT,
        error_message  TEXT,
        attempts       INTEGER NOT NULL DEFAULT 0,
        requested_by   TEXT NOT NULL DEFAULT 'host',
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL,
        finished_at    TEXT
    );
    INSERT INTO jobs_v2 SELECT id, kind, song_id, stage, stage_progress, progress, device, lyrics_source,
        options_json, error_code, error_message, attempts, requested_by, created_at, updated_at, finished_at
        FROM jobs;
    DROP TABLE jobs;
    ALTER TABLE jobs_v2 RENAME TO jobs;
    CREATE INDEX jobs_stage ON jobs(stage);
    CREATE INDEX jobs_song ON jobs(song_id);
    """,
    # ---- v3: letra oficial só com texto (CR-001, ciclo 20261004-1742, T-006) -----------------------------
    # Origem 'online_plain' (LRCLIB com `plainLyrics` e sem `syncedLyrics`): fonte de texto para o alinhamento pela
    # voz, não selecionável. A v2 já está em bancos reais: reconstrução de novo, sem perder dados.
    """
    CREATE TABLE lyrics_candidates_v3 (
        id           TEXT PRIMARY KEY,               -- lrclib:<n> | whisper:<n> | manual:<n> | aligned:<n> | online_plain:<n>
        song_id      TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
        source       TEXT NOT NULL CHECK (source IN ('lrclib','whisper','manual','aligned','online_plain')),
        timing       TEXT NOT NULL CHECK (timing IN ('none','line','word')),
        meta_json    TEXT NOT NULL DEFAULT '{}',
        lyrics_json  TEXT NOT NULL,
        derived_from TEXT,
        created_at   TEXT NOT NULL
    );
    INSERT INTO lyrics_candidates_v3(id, song_id, source, timing, meta_json, lyrics_json, derived_from, created_at)
        SELECT id, song_id, source, timing, meta_json, lyrics_json, derived_from, created_at FROM lyrics_candidates;
    DROP TABLE lyrics_candidates;
    ALTER TABLE lyrics_candidates_v3 RENAME TO lyrics_candidates;
    CREATE INDEX lyrics_candidates_song ON lyrics_candidates(song_id);
    """,
    # ---- v4: modo convidado (ciclo 20261005-0109, T-004, ADR-0003 §6) ------------------------------------
    # Nome de quem pediu gravado no item (as sessões de convidado vivem só em memória; a fila persiste).
    # `jobs.requested_by` já existe desde a v1 (recebe "g_<n>"). Índices para as cotas por convidado.
    """
    ALTER TABLE queue_items ADD COLUMN added_by_name TEXT;    -- nome do convidado no pedido; NULL = host
    CREATE INDEX IF NOT EXISTS queue_items_added_by ON queue_items(added_by, status);
    CREATE INDEX IF NOT EXISTS jobs_requested_by ON jobs(requested_by, stage);
    """,
]

SCHEMA_VERSION = len(MIGRATIONS)


def utcnow() -> str:
    """Data ISO-8601 UTC no formato do contrato: 2026-10-03T23:50:12Z."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Aplica migrações pendentes em transação. Retorna a versão final."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise RuntimeError(f"banco em versão {current}, mais nova que o código ({SCHEMA_VERSION})")
    for version in range(current, SCHEMA_VERSION):
        conn.execute("BEGIN IMMEDIATE")
        try:
            for stmt in _split_sql(MIGRATIONS[version]):
                conn.execute(stmt)
            conn.execute(f"PRAGMA user_version = {version + 1}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return SCHEMA_VERSION


def _split_sql(script: str) -> list[str]:
    stmts, buf = [], []
    for line in script.splitlines():
        buf.append(line)
        if sqlite3.complete_statement("\n".join(buf) + "\n"):
            stmt = "\n".join(buf).strip()
            if stmt.strip(";").strip():
                stmts.append(stmt)
            buf = []
    return stmts


def next_id(conn: sqlite3.Connection, name: str) -> int:
    """Próximo inteiro do contador `name` (use dentro de uma transação)."""
    row = conn.execute(
        "INSERT INTO counters(name, value) VALUES (?, 1) "
        "ON CONFLICT(name) DO UPDATE SET value = value + 1 RETURNING value",
        (name,),
    ).fetchone()
    return int(row[0])


class Database:
    def __init__(self, path: Path | str):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        return connect(self.path)

    def migrate(self) -> int:
        conn = self.connect()
        try:
            return migrate(conn)
        finally:
            conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Transação `BEGIN IMMEDIATE` com commit/rollback; conexão fechada no fim."""
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()
