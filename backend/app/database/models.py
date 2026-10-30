"""
Plain data classes for rows in the Phase 2 tables.

These are deliberately simple (no ORM magic): each class mirrors one table,
and `from_row()` turns a sqlite3.Row into a typed Python object so the rest of
the code gets attribute access and type hints instead of raw tuples.
"""

import sqlite3
from dataclasses import dataclass, fields
from enum import StrEnum
from typing import Self


class CrawlJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UrlStatus(StrEnum):
    PENDING = "pending"        # discovered, not fetched yet
    FETCHED = "fetched"        # fetched and stored as a document
    FAILED = "failed"          # network error / bad HTTP status
    SKIPPED = "skipped"        # wrong content type, too deep, off-domain...
    DUPLICATE = "duplicate"    # content identical to an existing document
    BLOCKED = "blocked"        # disallowed by robots.txt or security rules


class _Row:
    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Self:
        names = {f.name for f in fields(cls)}  # type: ignore[arg-type]
        return cls(**{k: row[k] for k in row.keys() if k in names})


@dataclass(frozen=True)
class Document(_Row):
    id: int
    url: str
    canonical_url: str
    title: str
    content: str
    author: str | None
    domain: str
    content_type: str
    language: str | None
    http_status: int | None
    content_hash: str
    crawl_job_id: int | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class CrawlJob(_Row):
    id: int
    seed_url: str
    status: str
    max_depth: int | None
    max_pages: int | None
    pages_discovered: int
    pages_fetched: int
    pages_failed: int
    error: str | None
    created_at: str
    started_at: str | None
    completed_at: str | None


@dataclass(frozen=True)
class UrlRecord(_Row):
    id: int
    url: str
    normalized_url: str
    domain: str
    status: str
    http_status: int | None
    depth: int | None
    attempts: int
    last_error: str | None
    content_hash: str | None
    document_id: int | None
    crawl_job_id: int | None
    discovered_at: str
    last_fetched_at: str | None
    next_crawl_at: str | None
