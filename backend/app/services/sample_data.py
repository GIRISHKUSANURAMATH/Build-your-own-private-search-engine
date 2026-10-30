"""Load the demonstration collection (data/sample/documents.json) into the database and index."""

import json
from pathlib import Path

from app.config import PROJECT_ROOT
from app.services.document_service import DocumentCreate
from app.services.index_service import IndexService

SAMPLE_FILE = PROJECT_ROOT / "data" / "sample" / "documents.json"
EVAL_FILE = PROJECT_ROOT / "data" / "eval" / "queries.json"


def read_sample(path: Path = SAMPLE_FILE) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_sample(index_service: IndexService, path: Path = SAMPLE_FILE) -> dict:
    """Insert (or update) every sample document and index it. Returns slug -> doc id."""
    created = updated = 0
    slug_to_id: dict[str, int] = {}
    for item in read_sample(path):
        result = index_service.ingest(DocumentCreate(
            url=item["url"], title=item["title"], content=item["content"],
            author=item.get("author"), content_type="text/plain", language="en",
        ))
        slug_to_id[item["slug"]] = result.document.id
        created += result.created
        updated += not result.created
    return {"created": created, "updated": updated, "slugs": slug_to_id}
