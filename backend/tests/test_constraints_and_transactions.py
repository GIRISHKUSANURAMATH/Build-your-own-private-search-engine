"""Phase 2: database constraints and transaction rollback."""

import sqlite3

import pytest

from app.database.database import get_connection
from app.database.repositories import UrlRepository

HASH = "a" * 64


def insert_doc(conn, **overrides):
    values = dict(url="https://x.com/", canonical_url="https://x.com/", domain="x.com", content_hash=HASH)
    values.update(overrides)
    cols = ", ".join(values)
    conn.execute(f"INSERT INTO documents ({cols}) VALUES ({', '.join('?' for _ in values)})", list(values.values()))


# ---------------------------------------------------------------- transactions
def test_get_connection_rolls_back_on_error(db_path):
    with pytest.raises(RuntimeError):
        with get_connection(db_path) as conn:
            insert_doc(conn)
            conn.execute("INSERT INTO crawl_jobs (seed_url) VALUES ('https://x.com/')")
            raise RuntimeError("boom")
    with get_connection(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM crawl_jobs").fetchone()[0] == 0


def test_get_connection_commits_on_success(db_path):
    with get_connection(db_path) as conn:
        insert_doc(conn)
    with get_connection(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1


def test_multi_table_write_is_all_or_nothing(crawl, documents, monkeypatch):
    """If the URL-state step of record_fetch_success fails, the document insert
    and the counter bump that already ran in the same transaction are undone."""
    job = crawl.create_job("https://example.com")
    crawl.start_job(job.id)

    def broken_upsert(self, *a, **kw):
        raise sqlite3.OperationalError("disk I/O error (simulated)")

    monkeypatch.setattr(UrlRepository, "upsert_state", broken_upsert)
    with pytest.raises(sqlite3.OperationalError):
        crawl.record_fetch_success(job.id, "https://example.com/page", content="text")

    assert documents.count_documents() == 0
    assert crawl.get_job(job.id).pages_fetched == 0


def test_failed_constraint_mid_transaction_rolls_back_earlier_rows(db_path):
    with pytest.raises(sqlite3.IntegrityError):
        with get_connection(db_path) as conn:
            insert_doc(conn, canonical_url="https://x.com/1")
            insert_doc(conn, canonical_url="https://x.com/2")
            insert_doc(conn, canonical_url="https://x.com/1")  # UNIQUE violation
    with get_connection(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


# ---------------------------------------------------------------- constraints
@pytest.mark.parametrize(
    "overrides",
    [
        {"content_hash": "tooshort"},             # SHA-256 hex must be 64 chars
        {"http_status": 999},                     # not a real HTTP status
        {"http_status": 42},
        {"canonical_url": None},                  # NOT NULL
        {"domain": None},
        {"crawl_job_id": 12345},                  # FOREIGN KEY to a job that doesn't exist
    ],
)
def test_document_constraints(db_path, overrides):
    with pytest.raises(sqlite3.IntegrityError):
        with get_connection(db_path) as conn:
            insert_doc(conn, **overrides)


def test_document_canonical_url_unique(db_path):
    with get_connection(db_path) as conn:
        insert_doc(conn)
    with pytest.raises(sqlite3.IntegrityError, match="documents.canonical_url"):
        with get_connection(db_path) as conn:
            insert_doc(conn, url="https://X.com")


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO crawl_jobs (seed_url, status) VALUES ('https://x.com/', 'exploded')",
        "INSERT INTO crawl_jobs (seed_url, pages_fetched) VALUES ('https://x.com/', -1)",
        "INSERT INTO crawl_jobs (seed_url, max_pages) VALUES ('https://x.com/', 0)",
        "INSERT INTO crawl_jobs (seed_url, max_depth) VALUES ('https://x.com/', -1)",
        "INSERT INTO crawl_jobs (seed_url, completed_at) VALUES ('https://x.com/', '2026-01-01')",  # finished, never started
        "INSERT INTO crawl_jobs (seed_url) VALUES (NULL)",
    ],
)
def test_crawl_job_constraints(db_path, sql):
    with pytest.raises(sqlite3.IntegrityError):
        with get_connection(db_path) as conn:
            conn.execute(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO urls (url, normalized_url, domain, status) VALUES ('u', 'n', 'd', 'lost')",
        "INSERT INTO urls (url, normalized_url, domain, attempts) VALUES ('u', 'n', 'd', -1)",
        "INSERT INTO urls (url, normalized_url, domain, depth) VALUES ('u', 'n', 'd', -1)",
        "INSERT INTO urls (url, normalized_url, domain, content_hash) VALUES ('u', 'n', 'd', 'abc')",
        "INSERT INTO urls (url, normalized_url, domain, crawl_job_id) VALUES ('u', 'n', 'd', 777)",
        "INSERT INTO urls (url, normalized_url, domain, document_id) VALUES ('u', 'n', 'd', 777)",
    ],
)
def test_url_constraints(db_path, sql):
    with pytest.raises(sqlite3.IntegrityError):
        with get_connection(db_path) as conn:
            conn.execute(sql)


def test_url_normalized_url_unique(db_path):
    with pytest.raises(sqlite3.IntegrityError, match="urls.normalized_url"):
        with get_connection(db_path) as conn:
            conn.execute("INSERT INTO urls (url, normalized_url, domain) VALUES ('a', 'same', 'd')")
            conn.execute("INSERT INTO urls (url, normalized_url, domain) VALUES ('b', 'same', 'd')")


def test_parameter_binding_blocks_sql_injection(documents):
    """A malicious title is stored as plain text, not executed."""
    from app.services.document_service import DocumentCreate

    evil = "x'); DROP TABLE documents; --"
    doc = documents.create_document(DocumentCreate(url="https://x.com/", title=evil))
    assert documents.get_document(doc.id).title == evil
    assert documents.count_documents() == 1
