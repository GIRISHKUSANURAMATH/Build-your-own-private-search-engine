"""Phase 2: document CRUD and canonical-URL handling."""

import time

import pytest

from app.database.database import get_connection
from app.database.repositories import DuplicateDocumentError, NotFoundError
from app.services.document_service import DocumentCreate, compute_content_hash
from app.utils.urls import InvalidURLError


def make(documents, url="https://example.com/python", **kw):
    kw.setdefault("title", "Python Tutorial")
    kw.setdefault("content", "Python is a powerful programming language.")
    return documents.create_document(DocumentCreate(url=url, **kw))


# ---------------------------------------------------------------- create
def test_create_document_stores_all_fields(documents):
    doc = make(
        documents, url="HTTPS://Example.com:443/python?utm_source=x#intro",
        author="Girish", language="en", http_status=200, content_type="text/html",
    )
    assert doc.id == 1
    assert doc.url == "HTTPS://Example.com:443/python?utm_source=x#intro"  # original kept
    assert doc.canonical_url == "https://example.com/python"               # normalized
    assert doc.domain == "example.com"
    assert doc.title == "Python Tutorial"
    assert doc.author == "Girish"
    assert doc.language == "en"
    assert doc.http_status == 200
    assert doc.content_hash == compute_content_hash(doc.content)
    assert len(doc.content_hash) == 64
    assert doc.created_at.endswith("Z") and doc.created_at == doc.updated_at


def test_create_rejects_invalid_url(documents):
    with pytest.raises(InvalidURLError):
        make(documents, url="ftp://example.com/x")
    assert documents.count_documents() == 0


# ---------------------------------------------------------------- read
def test_get_document_by_id(documents):
    created = make(documents)
    assert documents.get_document(created.id) == created


def test_get_missing_document_returns_none(documents):
    assert documents.get_document(999) is None


def test_get_document_by_any_url_spelling(documents):
    created = make(documents, url="https://example.com/python")
    assert documents.get_document_by_url("HTTPS://EXAMPLE.com:443/python#top") == created
    assert documents.get_document_by_url("https://example.com/java") is None


def test_list_and_count_with_pagination(documents):
    for i in range(5):
        make(documents, url=f"https://example.com/p{i}", content=f"page {i}")
    assert documents.count_documents() == 5
    assert [d.canonical_url for d in documents.list_documents(limit=2, offset=1)] == [
        "https://example.com/p1", "https://example.com/p2",
    ]


# ---------------------------------------------------------------- duplicates
def test_same_canonical_url_is_a_duplicate(documents):
    make(documents, url="https://example.com/python")
    with pytest.raises(DuplicateDocumentError) as err:
        make(documents, url="HTTPS://example.com:443/python?utm_campaign=z#x")
    assert err.value.canonical_url == "https://example.com/python"
    assert documents.count_documents() == 1


def test_different_urls_are_not_duplicates(documents):
    make(documents, url="https://example.com/python")
    make(documents, url="https://example.com/python/")  # trailing slash = different resource
    assert documents.count_documents() == 2


def test_content_hash_ignores_whitespace_differences():
    assert compute_content_hash("Python  is\n\tgreat ") == compute_content_hash("Python is great")
    assert compute_content_hash("Python is great") != compute_content_hash("python is great")


def test_find_documents_with_identical_content(documents):
    a = make(documents, url="https://a.com/x", content="Same text here")
    make(documents, url="https://b.com/y", content="Different")
    c = make(documents, url="https://c.com/z", content="Same   text\nhere")
    assert [d.id for d in documents.find_by_content("Same text here")] == [a.id, c.id]


# ---------------------------------------------------------------- update
def test_update_title_changes_updated_at_only(documents):
    doc = make(documents)
    time.sleep(0.01)  # timestamps have millisecond precision
    updated = documents.update_document(doc.id, title="New title")
    assert updated.title == "New title"
    assert updated.created_at == doc.created_at
    assert updated.updated_at > doc.updated_at
    assert updated.content_hash == doc.content_hash


def test_update_content_recomputes_hash(documents):
    doc = make(documents)
    updated = documents.update_document(doc.id, content="Completely new content")
    assert updated.content_hash == compute_content_hash("Completely new content")
    assert updated.content_hash != doc.content_hash


def test_update_url_recomputes_canonical_url_and_domain(documents):
    doc = make(documents)
    updated = documents.update_document(doc.id, url="https://Other.org/py#a")
    assert updated.canonical_url == "https://other.org/py"
    assert updated.domain == "other.org"


def test_update_url_to_existing_canonical_is_rejected(documents):
    make(documents, url="https://example.com/a")
    b = make(documents, url="https://example.com/b")
    with pytest.raises(DuplicateDocumentError):
        documents.update_document(b.id, url="https://EXAMPLE.com/a")
    assert documents.get_document(b.id).canonical_url == "https://example.com/b"


def test_update_rejects_derived_or_unknown_fields(documents):
    doc = make(documents)
    for field in ("canonical_url", "content_hash", "domain", "id", "created_at", "bogus"):
        with pytest.raises(ValueError):
            documents.update_document(doc.id, **{field: "x"})


def test_update_missing_document_raises(documents):
    with pytest.raises(NotFoundError):
        documents.update_document(42, title="x")


# ---------------------------------------------------------------- delete
def test_delete_document(documents):
    doc = make(documents)
    documents.delete_document(doc.id)
    assert documents.get_document(doc.id) is None
    assert documents.count_documents() == 0


def test_delete_missing_document_raises(documents):
    with pytest.raises(NotFoundError):
        documents.delete_document(42)


def test_deleting_document_keeps_url_history(db_path, documents, crawl):
    job = crawl.create_job("https://example.com/")
    crawl.start_job(job.id)
    doc = crawl.record_fetch_success(job.id, "https://example.com/python", content="hi")
    documents.delete_document(doc.id)
    state = crawl.get_url_state("https://example.com/python")
    assert state is not None and state.status == "fetched"
    assert state.document_id is None  # ON DELETE SET NULL


def test_document_linked_to_deleted_crawl_job_survives(db_path, documents, crawl):
    job = crawl.create_job("https://example.com/")
    doc = make(documents, crawl_job_id=job.id)
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM crawl_jobs WHERE id = ?", (job.id,))
    assert documents.get_document(doc.id).crawl_job_id is None
