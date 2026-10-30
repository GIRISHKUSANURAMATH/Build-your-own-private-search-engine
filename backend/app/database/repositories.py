"""
Repositories: the ONLY place that writes SQL for the Phase 2 tables.

Each repository wraps a sqlite3.Connection it is given. It never commits or
opens its own connection — the caller (a service) decides where a transaction
starts and ends. That is what lets one service method do "insert document +
update URL state + bump job counter" atomically: all three repositories share
one connection and therefore one transaction.

Column names in UPDATE statements come from fixed whitelists, never from user
input, so building SQL with f-strings here is safe. All VALUES use `?`
placeholders (parameter binding), which prevents SQL injection.
"""

import sqlite3
from typing import Any

from app.database.models import CrawlJob, Document, UrlRecord

NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


class RepositoryError(Exception):
    pass


class NotFoundError(RepositoryError):
    pass


class DuplicateDocumentError(RepositoryError):
    """A document with this canonical URL already exists."""

    def __init__(self, canonical_url: str):
        super().__init__(f"A document with canonical URL {canonical_url!r} already exists")
        self.canonical_url = canonical_url


def _set_clause(changes: dict[str, Any], allowed: frozenset[str]) -> tuple[str, list[Any]]:
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"Cannot update column(s): {', '.join(sorted(unknown))}")
    if not changes:
        raise ValueError("No changes given")
    columns = sorted(changes)
    return ", ".join(f"{c} = ?" for c in columns), [changes[c] for c in columns]


# ------------------------------------------------------------------ documents
class DocumentRepository:
    INSERT_COLUMNS = (
        "url", "canonical_url", "title", "content", "author", "domain",
        "content_type", "language", "http_status", "content_hash", "crawl_job_id",
    )
    UPDATABLE = frozenset(INSERT_COLUMNS)

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(self, **values: Any) -> Document:
        missing = {"url", "canonical_url", "domain", "content_hash"} - values.keys()
        if missing:
            raise ValueError(f"Missing required field(s): {', '.join(sorted(missing))}")
        unknown = set(values) - set(self.INSERT_COLUMNS)
        if unknown:
            raise ValueError(f"Unknown field(s): {', '.join(sorted(unknown))}")
        columns = [c for c in self.INSERT_COLUMNS if c in values]
        placeholders = ", ".join("?" for _ in columns)
        try:
            cur = self.conn.execute(
                f"INSERT INTO documents ({', '.join(columns)}) VALUES ({placeholders})",
                [values[c] for c in columns],
            )
        except sqlite3.IntegrityError as exc:
            if "documents.canonical_url" in str(exc):
                raise DuplicateDocumentError(values["canonical_url"]) from exc
            raise
        return self.get_or_raise(cur.lastrowid)

    def get(self, doc_id: int) -> Document | None:
        row = self.conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return Document.from_row(row) if row else None

    def get_or_raise(self, doc_id: int) -> Document:
        doc = self.get(doc_id)
        if doc is None:
            raise NotFoundError(f"Document {doc_id} not found")
        return doc

    def get_by_canonical_url(self, canonical_url: str) -> Document | None:
        row = self.conn.execute(
            "SELECT * FROM documents WHERE canonical_url = ?", (canonical_url,)
        ).fetchone()
        return Document.from_row(row) if row else None

    def find_by_content_hash(self, content_hash: str) -> list[Document]:
        rows = self.conn.execute(
            "SELECT * FROM documents WHERE content_hash = ? ORDER BY id", (content_hash,)
        ).fetchall()
        return [Document.from_row(r) for r in rows]

    def update(self, doc_id: int, **changes: Any) -> Document:
        clause, params = _set_clause(changes, self.UPDATABLE)
        try:
            cur = self.conn.execute(f"UPDATE documents SET {clause} WHERE id = ?", [*params, doc_id])
        except sqlite3.IntegrityError as exc:
            if "documents.canonical_url" in str(exc):
                raise DuplicateDocumentError(changes.get("canonical_url", "")) from exc
            raise
        if cur.rowcount == 0:
            raise NotFoundError(f"Document {doc_id} not found")
        return self.get_or_raise(doc_id)

    def delete(self, doc_id: int) -> bool:
        return self.conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,)).rowcount > 0

    def list(self, limit: int = 50, offset: int = 0) -> list[Document]:
        rows = self.conn.execute(
            "SELECT * FROM documents ORDER BY id LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        return [Document.from_row(r) for r in rows]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]


# ------------------------------------------------------------------ crawl jobs
class CrawlJobRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(self, seed_url: str, max_depth: int | None = None, max_pages: int | None = None) -> CrawlJob:
        cur = self.conn.execute(
            "INSERT INTO crawl_jobs (seed_url, max_depth, max_pages) VALUES (?, ?, ?)",
            (seed_url, max_depth, max_pages),
        )
        return self.get_or_raise(cur.lastrowid)

    def get(self, job_id: int) -> CrawlJob | None:
        row = self.conn.execute("SELECT * FROM crawl_jobs WHERE id = ?", (job_id,)).fetchone()
        return CrawlJob.from_row(row) if row else None

    def get_or_raise(self, job_id: int) -> CrawlJob:
        job = self.get(job_id)
        if job is None:
            raise NotFoundError(f"Crawl job {job_id} not found")
        return job

    def set_status(
        self,
        job_id: int,
        status: str,
        *,
        mark_started: bool = False,
        mark_completed: bool = False,
        error: str | None = None,
    ) -> CrawlJob:
        sets = ["status = ?"]
        params: list[Any] = [status]
        if mark_started:
            sets.append(f"started_at = {NOW_SQL}")
        if mark_completed:
            sets.append(f"completed_at = {NOW_SQL}")
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        cur = self.conn.execute(f"UPDATE crawl_jobs SET {', '.join(sets)} WHERE id = ?", [*params, job_id])
        if cur.rowcount == 0:
            raise NotFoundError(f"Crawl job {job_id} not found")
        return self.get_or_raise(job_id)

    def increment_counters(self, job_id: int, *, discovered: int = 0, fetched: int = 0, failed: int = 0) -> None:
        # "x = x + ?" is done by SQLite atomically — no read-modify-write race in Python.
        cur = self.conn.execute(
            """
            UPDATE crawl_jobs
               SET pages_discovered = pages_discovered + ?,
                   pages_fetched    = pages_fetched + ?,
                   pages_failed     = pages_failed + ?
             WHERE id = ?
            """,
            (discovered, fetched, failed, job_id),
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"Crawl job {job_id} not found")

    def list(self, status: str | None = None, limit: int = 50) -> list[CrawlJob]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM crawl_jobs WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM crawl_jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [CrawlJob.from_row(r) for r in rows]


# ------------------------------------------------------------------ URL state
class UrlRepository:
    STATE_COLUMNS = frozenset({
        "status", "http_status", "depth", "last_error", "content_hash",
        "document_id", "crawl_job_id", "last_fetched_at", "next_crawl_at",
    })

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, normalized_url: str) -> UrlRecord | None:
        row = self.conn.execute("SELECT * FROM urls WHERE normalized_url = ?", (normalized_url,)).fetchone()
        return UrlRecord.from_row(row) if row else None

    def add_if_new(
        self, url: str, normalized_url: str, domain: str, *, depth: int | None = None, crawl_job_id: int | None = None
    ) -> tuple[UrlRecord, bool]:
        """Insert a newly discovered URL. Returns (record, created).

        ON CONFLICT DO NOTHING makes "check if seen, then insert" a single
        atomic statement — this is the crawler's duplicate-URL check.
        """
        cur = self.conn.execute(
            """
            INSERT INTO urls (url, normalized_url, domain, depth, crawl_job_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(normalized_url) DO NOTHING
            """,
            (url, normalized_url, domain, depth, crawl_job_id),
        )
        record = self.get(normalized_url)
        assert record is not None
        return record, cur.rowcount == 1

    def upsert_state(
        self, url: str, normalized_url: str, domain: str, *, count_attempt: bool = False, **state: Any
    ) -> UrlRecord:
        """Insert the URL if unseen, otherwise update the given state columns."""
        unknown = set(state) - self.STATE_COLUMNS
        if unknown:
            raise ValueError(f"Unknown URL state field(s): {', '.join(sorted(unknown))}")
        columns = sorted(state)
        insert_cols = ["url", "normalized_url", "domain", "attempts", *columns]
        insert_vals = [url, normalized_url, domain, 1 if count_attempt else 0, *[state[c] for c in columns]]
        updates = [f"{c} = excluded.{c}" for c in columns]
        if count_attempt:
            updates.append("attempts = urls.attempts + 1")
        on_conflict = f"DO UPDATE SET {', '.join(updates)}" if updates else "DO NOTHING"
        self.conn.execute(
            f"""
            INSERT INTO urls ({', '.join(insert_cols)})
            VALUES ({', '.join('?' for _ in insert_cols)})
            ON CONFLICT(normalized_url) {on_conflict}
            """,
            insert_vals,
        )
        record = self.get(normalized_url)
        assert record is not None
        return record

    def list_by_status(self, status: str, limit: int = 100) -> list[UrlRecord]:
        """Due URLs first (NULL next_crawl_at = 'as soon as possible'). Uses idx_urls_status_next_crawl."""
        rows = self.conn.execute(
            """
            SELECT * FROM urls
             WHERE status = ? AND (next_crawl_at IS NULL OR next_crawl_at <= strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
             ORDER BY next_crawl_at
             LIMIT ?
            """,
            (status, limit),
        ).fetchall()
        return [UrlRecord.from_row(r) for r in rows]

    def list_for_job(self, crawl_job_id: int) -> list[UrlRecord]:
        rows = self.conn.execute(
            "SELECT * FROM urls WHERE crawl_job_id = ? ORDER BY id", (crawl_job_id,)
        ).fetchall()
        return [UrlRecord.from_row(r) for r in rows]
