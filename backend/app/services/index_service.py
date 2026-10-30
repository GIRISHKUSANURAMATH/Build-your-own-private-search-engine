"""
IndexService: document ingestion = store + index, as one operation.

The API and the crawler call this instead of DocumentService directly, so a
document can never be stored without being indexed (or deleted while its
postings stay behind).

    ingest_html / ingest_text ─► extract (Phase 3) ─► DocumentService.create/update
                                                   ─► SearchIndex.index_document
"""

from dataclasses import dataclass
from pathlib import Path

from app.database.models import Document
from app.database.repositories import NotFoundError
from app.indexing.html_extractor import extract_document
from app.indexing.indexer import SearchIndex
from app.services.document_service import DocumentCreate, DocumentService, compute_content_hash
from app.utils.urls import normalize_url


@dataclass
class IngestResult:
    document: Document
    created: bool          # False = an existing document (same canonical URL) was updated
    duplicate_of: int | None = None  # set when identical content exists under another URL


class IndexService:
    def __init__(self, db_path: Path, index: SearchIndex):
        self.db_path = db_path
        self.index = index
        self.documents = DocumentService(db_path)

    def ingest(self, data: DocumentCreate, *, allow_duplicate_content: bool = True) -> IngestResult:
        """Store a document (insert, or update if its canonical URL exists) and index it."""
        if not allow_duplicate_content:
            same = [d for d in self.documents.find_by_content(data.content)
                    if d.canonical_url != normalize_url(data.url)]
            if same:
                return IngestResult(same[0], created=False, duplicate_of=same[0].id)
        existing = self.documents.get_document_by_url(data.url)
        if existing:
            doc = self.documents.update_document(
                existing.id, url=data.url, title=data.title, content=data.content, author=data.author,
                content_type=data.content_type, language=data.language, http_status=data.http_status,
                crawl_job_id=data.crawl_job_id,
            )
            created = False
        else:
            doc = self.documents.create_document(data)
            created = True
        self.index.index_document(self.db_path, doc)
        return IngestResult(doc, created)

    def ingest_raw(self, url: str, raw: str | bytes, content_type: str = "text/html", **overrides) -> IngestResult:
        """Extract text + metadata from HTML/plain text, then ingest. Explicit
        overrides (title, author, language) win over extracted values."""
        extracted = extract_document(raw, content_type)
        data = DocumentCreate(
            url=url,
            title=overrides.get("title") or extracted.title,
            content=extracted.text,
            author=overrides.get("author") or extracted.author,
            content_type=content_type.split(";")[0].strip().lower(),
            language=overrides.get("language") or extracted.language,
            http_status=overrides.get("http_status"),
            crawl_job_id=overrides.get("crawl_job_id"),
        )
        return self.ingest(data, allow_duplicate_content=overrides.get("allow_duplicate_content", True))

    def delete(self, doc_id: int) -> None:
        if self.documents.get_document(doc_id) is None:
            raise NotFoundError(f"Document {doc_id} not found")
        self.index.remove_document(self.db_path, doc_id)
        self.documents.delete_document(doc_id)

    @staticmethod
    def content_hash(text: str) -> str:
        return compute_content_hash(text)
