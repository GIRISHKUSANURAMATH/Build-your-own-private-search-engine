"""Phase 2: schema migrations, versioning, indexes."""

import sqlite3
from contextlib import closing

import pytest

from app.database import migrations
from app.database.database import SCHEMA_VERSION, check_db, connect, init_db
from app.database.migrations import MigrationError, apply_migrations, get_version

PHASE2_TABLES = {"crawl_jobs", "documents", "urls"}
PHASE3_TABLES = {"terms", "postings", "doc_stats", "vocabulary", "search_history", "index_meta"}
EXPECTED_INDEXES = {
    "idx_documents_content_hash",
    "idx_documents_domain",
    "idx_documents_crawl_job",
    "idx_urls_crawl_job",
    "idx_urls_document",
    "idx_urls_status_next_crawl",
    "idx_crawl_jobs_status",
    "idx_postings_doc",
    "idx_search_history_created",
    "idx_search_history_query",
}


def _names(conn, kind):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = ?", (kind,))}


def test_latest_schema_version_is_3():
    assert SCHEMA_VERSION == 3


def test_migration_on_fresh_database(tmp_path):
    path = tmp_path / "fresh.db"
    init_db(path)
    conn = connect(path)
    assert get_version(conn) == 3
    assert PHASE2_TABLES | PHASE3_TABLES | {"schema_info"} <= _names(conn, "table")
    conn.close()
    # Fresh database: nothing to back up
    assert not list(tmp_path.glob("*.bak"))


def test_migration_on_existing_phase1_database(phase1_db):
    init_db(phase1_db)
    conn = connect(phase1_db)
    assert get_version(conn) == 3
    assert PHASE2_TABLES | PHASE3_TABLES <= _names(conn, "table")
    # The original Phase 1 row is preserved, not recreated
    row = conn.execute("SELECT id, created_at FROM schema_info").fetchall()
    assert [tuple(r) for r in row] == [(1, "2026-10-30 13:33:45")]
    conn.close()


def test_phase1_database_is_backed_up_before_upgrade(phase1_db):
    init_db(phase1_db)
    backup = phase1_db.with_name("phase1.db.v1.bak")
    assert backup.exists()
    # closing(): sqlite3's own `with` only commits, it does NOT close the connection
    with closing(sqlite3.connect(backup)) as b:
        assert b.execute("SELECT version FROM schema_info").fetchone()[0] == 1
        assert "documents" not in _names(b, "table")


def test_migration_is_idempotent(db_path, tmp_path):
    init_db(db_path)
    init_db(db_path)
    assert check_db(db_path)["schema_version"] == 3
    assert not list(tmp_path.glob("*.bak"))  # already current -> no backup made


def test_schema_version_increments_one_step_at_a_time(tmp_path):
    conn = connect(tmp_path / "steps.db")
    assert get_version(conn) == 0
    assert apply_migrations(conn, target=1) == [1]
    assert get_version(conn) == 1
    assert "documents" not in _names(conn, "table")
    assert apply_migrations(conn, target=2) == [2]
    assert get_version(conn) == 2
    assert "documents" in _names(conn, "table") and "postings" not in _names(conn, "table")
    assert apply_migrations(conn) == [3]
    assert get_version(conn) == 3
    assert apply_migrations(conn) == []  # nothing left
    conn.close()


def test_failed_migration_rolls_back_completely(db_path, monkeypatch):
    bad_version = migrations.LATEST_VERSION + 1
    bad = {**migrations.MIGRATIONS, bad_version: ["CREATE TABLE half_done (x INTEGER)", "THIS IS NOT SQL"]}
    monkeypatch.setattr(migrations, "MIGRATIONS", bad)
    conn = connect(db_path)
    with pytest.raises(MigrationError, match=f"v{bad_version}"):
        apply_migrations(conn, target=bad_version)
    assert get_version(conn) == migrations.LATEST_VERSION
    assert "half_done" not in _names(conn, "table")  # first statement undone too
    conn.close()


def test_refuses_database_from_newer_version(db_path):
    conn = connect(db_path)
    conn.execute("UPDATE schema_info SET version = 99")
    conn.commit()
    with pytest.raises(MigrationError, match="v99"):
        apply_migrations(conn)
    conn.close()


def test_expected_indexes_exist(db_path):
    conn = connect(db_path)
    assert EXPECTED_INDEXES <= _names(conn, "index")
    # UNIQUE columns get automatic indexes
    unique_cols = set()
    for table in ("documents", "urls"):
        for idx in conn.execute(f"PRAGMA index_list({table})"):
            if idx["unique"]:
                cols = [c["name"] for c in conn.execute(f"PRAGMA index_info({idx['name']})")]
                unique_cols.add(f"{table}.{cols[0]}")
    assert {"documents.canonical_url", "urls.normalized_url"} <= unique_cols
    conn.close()


@pytest.mark.parametrize(
    "sql, params, index",
    [
        ("SELECT id FROM documents WHERE content_hash = ?", ("a" * 64,), "idx_documents_content_hash"),
        ("SELECT id FROM documents WHERE domain = ?", ("x.com",), "idx_documents_domain"),
        ("SELECT id FROM urls WHERE status = ? ORDER BY next_crawl_at", ("pending",), "idx_urls_status_next_crawl"),
        ("SELECT id FROM urls WHERE crawl_job_id = ?", (1,), "idx_urls_crawl_job"),
    ],
)
def test_queries_actually_use_their_index(db_path, sql, params, index):
    """EXPLAIN QUERY PLAN shows SQLite's chosen strategy: an index search, not a table SCAN."""
    conn = connect(db_path)
    plan = " ".join(r["detail"] for r in conn.execute(f"EXPLAIN QUERY PLAN {sql}", params))
    assert index in plan, plan
    conn.close()


def test_updated_at_trigger_exists(db_path):
    conn = connect(db_path)
    assert "trg_documents_updated_at" in _names(conn, "trigger")
    conn.close()


def test_wal_and_foreign_keys_still_enabled(db_path):
    conn = connect(db_path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def test_health_reports_latest_schema(client):
    assert client.get("/health").json()["database"]["schema_version"] == 3
