"""
Versioned schema migrations.

HOW IT WORKS
------------
* `schema_info.version` stores which migration the database file is on.
* `MIGRATIONS` maps version number -> list of SQL statements.
* `apply_migrations()` runs every migration whose number is greater than the
  stored version, in order. Each migration runs inside its own transaction:
  either all of its statements succeed and the version is bumped, or nothing
  changes. A crash halfway through never leaves a half-migrated database.

RULES FOR FUTURE MIGRATIONS
---------------------------
* Never edit a migration that has already shipped. Add a new number instead.
* Migrations must be additive or explicitly copy data; never DROP user data.

TIMESTAMPS
----------
All timestamps are UTC ISO-8601 text with milliseconds, e.g.
'2026-10-30T13:33:45.123Z'. Text sorts chronologically, is human-readable in
any SQLite viewer, and millisecond precision lets tests observe updated_at
changing.
"""

import sqlite3

NOW = "(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"

# --- Version 1 (Phase 1) -------------------------------------------------------
# Identical to what Phase 1's init_db() created, so on an existing Phase 1
# database these statements are no-ops.
_V1 = [
    """
    CREATE TABLE IF NOT EXISTS schema_info (
        id          INTEGER PRIMARY KEY CHECK (id = 1),  -- exactly one row
        version     INTEGER NOT NULL,
        created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "INSERT OR IGNORE INTO schema_info (id, version) VALUES (1, 0)",
]

# --- Version 2 (Phase 2): crawl jobs, documents, URL state ---------------------
_V2 = [
    # One row per crawl run started by the user.
    f"""
    CREATE TABLE crawl_jobs (
        id                  INTEGER PRIMARY KEY,
        seed_url            TEXT    NOT NULL,
        status              TEXT    NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
        max_depth           INTEGER CHECK (max_depth IS NULL OR max_depth >= 0),
        max_pages           INTEGER CHECK (max_pages IS NULL OR max_pages >= 1),
        pages_discovered    INTEGER NOT NULL DEFAULT 0 CHECK (pages_discovered >= 0),
        pages_fetched       INTEGER NOT NULL DEFAULT 0 CHECK (pages_fetched >= 0),
        pages_failed        INTEGER NOT NULL DEFAULT 0 CHECK (pages_failed >= 0),
        error               TEXT,
        created_at          TEXT    NOT NULL DEFAULT {NOW},
        started_at          TEXT,
        completed_at        TEXT,
        -- a job cannot finish before it started
        CHECK (completed_at IS NULL OR started_at IS NOT NULL)
    )
    """,
    # One row per stored page. canonical_url is the identity of a document.
    f"""
    CREATE TABLE documents (
        id              INTEGER PRIMARY KEY,
        url             TEXT    NOT NULL,             -- URL as originally given/fetched
        canonical_url   TEXT    NOT NULL UNIQUE,      -- normalized form; one document per canonical URL
        title           TEXT    NOT NULL DEFAULT '',
        content         TEXT    NOT NULL DEFAULT '',
        author          TEXT,
        domain          TEXT    NOT NULL,
        content_type    TEXT    NOT NULL DEFAULT 'text/html',
        language        TEXT,
        http_status     INTEGER CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
        content_hash    TEXT    NOT NULL CHECK (length(content_hash) = 64),  -- SHA-256 hex
        crawl_job_id    INTEGER REFERENCES crawl_jobs(id) ON DELETE SET NULL,
        created_at      TEXT    NOT NULL DEFAULT {NOW},
        updated_at      TEXT    NOT NULL DEFAULT {NOW}
    )
    """,
    # Crawl state of every URL we have ever seen (the persistent part of the frontier).
    f"""
    CREATE TABLE urls (
        id                  INTEGER PRIMARY KEY,
        url                 TEXT    NOT NULL,          -- as discovered (e.g. href value, resolved)
        normalized_url      TEXT    NOT NULL UNIQUE,   -- one state row per normalized URL
        domain              TEXT    NOT NULL,
        status              TEXT    NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'fetched', 'failed', 'skipped', 'duplicate', 'blocked')),
        http_status         INTEGER CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
        depth               INTEGER CHECK (depth IS NULL OR depth >= 0),
        attempts            INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        last_error          TEXT,
        content_hash        TEXT    CHECK (content_hash IS NULL OR length(content_hash) = 64),
        document_id         INTEGER REFERENCES documents(id)  ON DELETE SET NULL,
        crawl_job_id        INTEGER REFERENCES crawl_jobs(id) ON DELETE SET NULL,
        discovered_at       TEXT    NOT NULL DEFAULT {NOW},
        last_fetched_at     TEXT,
        next_crawl_at       TEXT
    )
    """,
    # Keep documents.updated_at honest without relying on every caller to set it.
    # The WHEN clause stops the trigger re-firing on its own UPDATE.
    f"""
    CREATE TRIGGER trg_documents_updated_at
    AFTER UPDATE ON documents
    FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
    BEGIN
        UPDATE documents SET updated_at = {NOW} WHERE id = NEW.id;
    END
    """,
    # ---- Indexes (UNIQUE columns above already get an automatic index) ----
    # Duplicate-content check: "is there already a document with this hash?"
    # Runs for every fetched page, so it must not scan the table. Not UNIQUE on
    # purpose: the dedup *policy* belongs in code (Phase 9), not in a constraint.
    "CREATE INDEX idx_documents_content_hash ON documents(content_hash)",
    # site: filter (Phase 8) and per-domain statistics (Phase 11).
    "CREATE INDEX idx_documents_domain ON documents(domain)",
    # Foreign-key columns. SQLite does NOT index these automatically; without
    # them, deleting a crawl job or document scans the whole child table to
    # apply ON DELETE SET NULL, and "pages of job N" is a full scan.
    "CREATE INDEX idx_documents_crawl_job ON documents(crawl_job_id)",
    "CREATE INDEX idx_urls_crawl_job ON urls(crawl_job_id)",
    "CREATE INDEX idx_urls_document ON urls(document_id)",
    # Scheduler query: "URLs in status X that are due" ->
    #   WHERE status = 'pending' ORDER BY next_crawl_at
    # Composite index: equality column first, range/sort column second.
    "CREATE INDEX idx_urls_status_next_crawl ON urls(status, next_crawl_at)",
    # Crawl dashboard: list jobs by status (e.g. find the running job).
    "CREATE INDEX idx_crawl_jobs_status ON crawl_jobs(status)",
]

# --- Version 3 (Phases 4-11): inverted index, vocabulary, search history ------
_V3 = [
    # One row per (field, term). df = document frequency = number of documents
    # whose `field` contains the term. Kept here for SQL-side statistics; the
    # in-memory index is the authority at search time.
    """
    CREATE TABLE terms (
        id      INTEGER PRIMARY KEY,
        field   TEXT    NOT NULL CHECK (field IN ('body', 'title')),
        term    TEXT    NOT NULL,
        df      INTEGER NOT NULL DEFAULT 0 CHECK (df >= 0),
        UNIQUE (field, term)
    )
    """,
    # The postings lists: one row per (term, document).
    # positions = word positions of the term in that document, stored as a
    # packed array of 32-bit unsigned ints (4 bytes each) — far smaller than
    # JSON text. WITHOUT ROWID stores rows ordered by the primary key, so all
    # postings of one term sit together on disk.
    """
    CREATE TABLE postings (
        term_id     INTEGER NOT NULL REFERENCES terms(id)     ON DELETE CASCADE,
        doc_id      INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        tf          INTEGER NOT NULL CHECK (tf > 0),
        positions   BLOB    NOT NULL,
        PRIMARY KEY (term_id, doc_id)
    ) WITHOUT ROWID
    """,
    # "Remove every posting of document N" (re-indexing or deleting a document)
    # needs the forward direction doc -> terms; the primary key only covers term -> doc.
    "CREATE INDEX idx_postings_doc ON postings(doc_id)",
    # Per-document statistics needed by BM25 (document length) and to detect
    # documents whose content changed since they were indexed (content_hash).
    f"""
    CREATE TABLE doc_stats (
        doc_id          INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
        body_length     INTEGER NOT NULL CHECK (body_length >= 0),
        title_length    INTEGER NOT NULL CHECK (title_length >= 0),
        content_hash    TEXT    NOT NULL,
        indexed_at      TEXT    NOT NULL DEFAULT {NOW}
    )
    """,
    # Real (unstemmed) words seen in the collection -> their index term.
    # Fuzzy matching, "did you mean" and autocomplete must work with real words
    # ("machine"), not stems ("machin"). A word's popularity is the df of its
    # term, so no per-word counter can go stale when documents are deleted;
    # words whose term no longer occurs anywhere are simply ignored.
    """
    CREATE TABLE vocabulary (
        word    TEXT    PRIMARY KEY,
        term    TEXT    NOT NULL
    ) WITHOUT ROWID
    """,
    f"""
    CREATE TABLE search_history (
        id              INTEGER PRIMARY KEY,
        query           TEXT    NOT NULL,
        mode            TEXT    NOT NULL DEFAULT 'basic',
        ranker          TEXT    NOT NULL DEFAULT 'bm25',
        results_count   INTEGER NOT NULL DEFAULT 0 CHECK (results_count >= 0),
        latency_ms      REAL    NOT NULL DEFAULT 0,
        clicked_doc_id  INTEGER REFERENCES documents(id) ON DELETE SET NULL,
        created_at      TEXT    NOT NULL DEFAULT {NOW}
    )
    """,
    # Recent searches (ORDER BY created_at DESC) and popular / prefix queries (GROUP BY query).
    "CREATE INDEX idx_search_history_created ON search_history(created_at)",
    "CREATE INDEX idx_search_history_query ON search_history(query)",
    # Small key/value table: last indexing time, analyzer configuration, etc.
    """
    CREATE TABLE index_meta (
        key     TEXT PRIMARY KEY,
        value   TEXT NOT NULL
    ) WITHOUT ROWID
    """,
]

MIGRATIONS: dict[int, list[str]] = {
    1: _V1,
    2: _V2,
    3: _V3,
}

LATEST_VERSION = max(MIGRATIONS)


class MigrationError(RuntimeError):
    pass


def get_version(conn: sqlite3.Connection) -> int:
    """Current schema version; 0 for a brand-new empty database."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_info'"
    ).fetchone()
    if not exists:
        return 0
    row = conn.execute("SELECT version FROM schema_info WHERE id = 1").fetchone()
    return int(row[0]) if row else 0


def pending_migrations(conn: sqlite3.Connection) -> list[int]:
    current = get_version(conn)
    return [v for v in sorted(MIGRATIONS) if v > current]


def apply_migrations(conn: sqlite3.Connection, target: int = LATEST_VERSION) -> list[int]:
    """Apply all migrations up to `target`. Returns the versions applied."""
    current = get_version(conn)
    if current > LATEST_VERSION:
        raise MigrationError(
            f"Database is at schema v{current} but this code only knows up to v{LATEST_VERSION}. "
            "Refusing to touch it — are you running an older version of PySearch?"
        )

    applied: list[int] = []
    for version in sorted(MIGRATIONS):
        if version > target:
            break
        # BEGIN IMMEDIATE takes the write lock now, so two processes starting
        # at once cannot both run the same migration.
        conn.execute("BEGIN IMMEDIATE")
        try:
            if get_version(conn) >= version:  # re-check under the lock
                conn.execute("ROLLBACK")
                continue
            for statement in MIGRATIONS[version]:
                conn.execute(statement)
            conn.execute("UPDATE schema_info SET version = ? WHERE id = 1", (version,))
            conn.execute("COMMIT")
            applied.append(version)
        except Exception as exc:
            conn.execute("ROLLBACK")
            raise MigrationError(f"Migration to v{version} failed and was rolled back: {exc}") from exc
    return applied
