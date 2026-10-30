"""
SQLite connection handling.

Design decision: we use Python's built-in `sqlite3` module, not an ORM.
- The heavy part of a search engine is the index, and we want to see every
  query that touches it (bulk-inserting postings through an ORM is slow and
  hides what is happening).
- Fewer layers = easier to explain in a viva.

The `schema_info` table records which schema version the database file is
on. Tables are created by numbered migrations (see migrations.py); init_db()
runs whichever ones the file hasn't had yet.
"""

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.database.migrations import LATEST_VERSION, apply_migrations, get_version, pending_migrations

logger = logging.getLogger("pysearch.database")

SCHEMA_VERSION = LATEST_VERSION


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with sensible defaults."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row             # rows behave like dicts: row["title"]
    conn.execute("PRAGMA foreign_keys = ON")   # SQLite ignores FOREIGN KEY unless told otherwise
    conn.execute("PRAGMA journal_mode = WAL")  # readers don't block the writer (indexer vs. search)
    return conn


@contextmanager
def get_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on error, always close."""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def backup_db(conn: sqlite3.Connection, db_path: Path, version: int) -> Path:
    """Copy the live database to '<name>.v<version>.bak' using SQLite's online backup API
    (safe even in WAL mode, unlike copying the file)."""
    backup_path = db_path.with_name(f"{db_path.name}.v{version}.bak")
    with sqlite3.connect(backup_path) as dest:
        conn.backup(dest)
    dest.close()
    return backup_path


def init_db(db_path: Path) -> None:
    """Create the database if missing and bring its schema up to SCHEMA_VERSION.

    Safe to call on every startup: already-applied migrations are skipped.
    Before upgrading an existing (non-empty) database, a backup copy is written
    next to it, so a bad migration can never cost you your data.
    """
    conn = connect(db_path)
    try:
        current = get_version(conn)
        pending = pending_migrations(conn)
        if current > 0 and pending:
            backup = backup_db(conn, db_path, current)
            logger.info("Backed up schema v%s database to %s", current, backup)
        applied = apply_migrations(conn)
        if applied:
            logger.info("Applied migrations %s (schema v%s -> v%s)", applied, current, get_version(conn))
    finally:
        conn.close()


def check_db(db_path: Path) -> dict:
    """Used by /health: prove we can actually read from the database."""
    with get_connection(db_path) as conn:
        version = conn.execute("SELECT version FROM schema_info WHERE id = 1").fetchone()
        sqlite_version = conn.execute("SELECT sqlite_version()").fetchone()[0]
    return {
        "ok": version is not None,
        "schema_version": version["version"] if version else None,
        "sqlite_version": sqlite_version,
        "path": str(db_path),
    }
