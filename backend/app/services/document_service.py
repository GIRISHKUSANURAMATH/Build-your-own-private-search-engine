"""
Document service: the public Python API for storing documents.

Responsibilities that do NOT belong in the repository:
* turning a raw URL into canonical_url + domain (normalization)
* computing the content hash
* keeping derived fields consistent when a document is updated

Each public method is ONE transaction (one `get_connection` block): it either
fully happens or not at all.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from app.database.database import get_connection
from app.database.models import Document
from app.database.repositories import DocumentRepository, NotFoundError
from app.utils.urls import get_domain, normalize_url

_WHITESPACE = re.compile(r"\s+")


def compute_content_hash(content: str) -> str:
    """SHA-256 of the text with whitespace collapsed.

    Collapsing whitespace means two pages that differ only in indentation or
    line breaks get the same hash (they are the same content to a reader).
    64 hex characters; the database CHECK constraint enforces that length.
    """
    normalized = _WHITESPACE.sub(" ", content).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass
class DocumentCreate:
    url: str
    title: str = ""
    content: str = ""
    author: str | None = None
    content_type: str = "text/html"
    language: str | None = None
    http_status: int | None = None
    crawl_job_id: int | None = None


class DocumentService:
    # Fields a caller may change; canonical_url/domain/content_hash are derived.
    EDITABLE = frozenset({"url", "title", "content", "author", "content_type", "language", "http_status", "crawl_job_id"})

    def __init__(self, db_path: Path):
        self.db_path = db_path

    @staticmethod
    def _derived_fields(url: str | None = None, content: str | None = None) -> dict:
        derived: dict = {}
        if url is not None:
            derived["canonical_url"] = normalize_url(url)
            derived["domain"] = get_domain(url)
        if content is not None:
            derived["content_hash"] = compute_content_hash(content)
        return derived

    def create_document(self, data: DocumentCreate) -> Document:
        """Store a new document. Raises DuplicateDocumentError if its canonical URL exists."""
        values = {
            "url": data.url.strip(),
            "title": data.title,
            "content": data.content,
            "author": data.author,
            "content_type": data.content_type,
            "language": data.language,
            "http_status": data.http_status,
            "crawl_job_id": data.crawl_job_id,
            **self._derived_fields(url=data.url, content=data.content),
        }
        with get_connection(self.db_path) as conn:
            return DocumentRepository(conn).insert(**values)

    def get_document(self, doc_id: int) -> Document | None:
        with get_connection(self.db_path) as conn:
            return DocumentRepository(conn).get(doc_id)

    def get_document_by_url(self, url: str) -> Document | None:
        """Look up by ANY spelling of the URL — it is normalized first."""
        with get_connection(self.db_path) as conn:
            return DocumentRepository(conn).get_by_canonical_url(normalize_url(url))

    def find_by_content(self, content: str) -> list[Document]:
        """Documents whose content hashes the same (exact-duplicate check)."""
        with get_connection(self.db_path) as conn:
            return DocumentRepository(conn).find_by_content_hash(compute_content_hash(content))

    def update_document(self, doc_id: int, **changes) -> Document:
        """Update editable fields; canonical_url/domain/hash are recomputed when url/content change."""
        unknown = set(changes) - self.EDITABLE
        if unknown:
            raise ValueError(f"Cannot update field(s): {', '.join(sorted(unknown))}")
        if "url" in changes:
            changes["url"] = changes["url"].strip()
        changes.update(self._derived_fields(url=changes.get("url"), content=changes.get("content")))
        with get_connection(self.db_path) as conn:
            return DocumentRepository(conn).update(doc_id, **changes)

    def delete_document(self, doc_id: int) -> None:
        """Delete a document. URL rows pointing at it keep their history (document_id -> NULL)."""
        with get_connection(self.db_path) as conn:
            if not DocumentRepository(conn).delete(doc_id):
                raise NotFoundError(f"Document {doc_id} not found")

    def list_documents(self, limit: int = 50, offset: int = 0) -> list[Document]:
        with get_connection(self.db_path) as conn:
            return DocumentRepository(conn).list(limit=limit, offset=offset)

    def count_documents(self) -> int:
        with get_connection(self.db_path) as conn:
            return DocumentRepository(conn).count()
