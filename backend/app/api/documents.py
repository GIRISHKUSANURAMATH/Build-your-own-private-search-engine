"""Document endpoints: add (and index), list, view, delete."""

from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, model_validator

from app.api import deps
from app.database.database import get_connection
from app.database.repositories import NotFoundError
from app.indexing.indexer import SearchIndex
from app.services.document_service import DocumentCreate
from app.services.index_service import IndexService

router = APIRouter(tags=["documents"])

MAX_CONTENT_CHARS = 2_000_000


class DocumentIn(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048, description="Identity of the document (http/https)")
    title: str | None = Field(None, max_length=500)
    content: str | None = Field(None, max_length=MAX_CONTENT_CHARS, description="Plain text, or HTML if format=html")
    format: Literal["text", "html"] = "text"
    author: str | None = Field(None, max_length=200)
    language: str | None = Field(None, max_length=20)

    @model_validator(mode="after")
    def _has_content(self):
        if not (self.content or "").strip():
            raise ValueError("content must not be empty")
        return self


def _doc_dict(doc, index: SearchIndex, full: bool = True) -> dict:
    d = {
        "id": doc.id, "url": doc.url, "canonical_url": doc.canonical_url, "title": doc.title,
        "domain": doc.domain, "author": doc.author, "language": doc.language,
        "content_type": doc.content_type, "http_status": doc.http_status, "content_hash": doc.content_hash,
        "crawl_job_id": doc.crawl_job_id, "created_at": doc.created_at, "updated_at": doc.updated_at,
        "indexed": index.is_indexed(doc.id), "length": len(doc.content),
    }
    if full:
        d["content"] = doc.content
        d["top_terms"] = [{"term": t, "tf": tf} for t, tf in index.forward.top_terms(doc.id, 15)]
        d["term_count"] = index.body.doc_lengths.get(doc.id, 0)
    else:
        d["preview"] = doc.content[:200]
    return d


@router.post("/documents", status_code=201)
def add_document(body: DocumentIn, response: Response,
                 svc: IndexService = Depends(deps.index_service), index: SearchIndex = Depends(deps.index)) -> dict:
    if body.format == "html":
        result = svc.ingest_raw(body.url, body.content, "text/html",
                                title=body.title, author=body.author, language=body.language)
    else:
        result = svc.ingest(DocumentCreate(url=body.url, title=body.title or "", content=body.content,
                                           author=body.author, content_type="text/plain", language=body.language))
    if not result.created:
        response.status_code = 200
    return {"created": result.created, "document": _doc_dict(result.document, index)}


@router.post("/documents/sample")
def load_sample_documents(svc: IndexService = Depends(deps.index_service),
                          index: SearchIndex = Depends(deps.index)) -> dict:
    """Load the 44-document demonstration collection (safe to run again: updates in place)."""
    from app.services.sample_data import load_sample
    result = load_sample(svc)
    return {"created": result["created"], "updated": result["updated"], "documents": index.doc_count}


@router.get("/documents")
def list_documents(
    limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0),
    domain: str | None = Query(None, max_length=255),
    svc: IndexService = Depends(deps.index_service), index: SearchIndex = Depends(deps.index),
) -> dict:
    with get_connection(svc.db_path) as conn:
        where, params = ("WHERE domain = ?", [domain]) if domain else ("", [])
        total = conn.execute(f"SELECT COUNT(*) FROM documents {where}", params).fetchone()[0]
        ids = [r[0] for r in conn.execute(
            f"SELECT id FROM documents {where} ORDER BY id DESC LIMIT ? OFFSET ?", [*params, limit, offset])]
    docs = [svc.documents.get_document(i) for i in ids]
    return {"total": total, "limit": limit, "offset": offset,
            "documents": [_doc_dict(d, index, full=False) for d in docs if d]}


@router.get("/documents/{doc_id}")
def get_document(doc_id: int, svc: IndexService = Depends(deps.index_service),
                 index: SearchIndex = Depends(deps.index)) -> dict:
    doc = svc.documents.get_document(doc_id)
    if doc is None:
        raise NotFoundError(f"Document {doc_id} not found")
    return _doc_dict(doc, index)


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: int, svc: IndexService = Depends(deps.index_service)) -> dict:
    svc.delete(doc_id)
    return {"deleted": doc_id}
