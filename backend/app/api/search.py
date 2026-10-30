"""Search endpoints: /search, /search/suggest, /search/history, /search/click."""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api import deps
from app.search.query_parser import MAX_QUERY_LENGTH
from app.search.search_engine import SearchEngine
from app.search.suggest import Suggester
from app.services.history_service import HistoryService

router = APIRouter(tags=["search"])


@router.get("/search")
def search(
    q: str = Query(..., min_length=1, max_length=MAX_QUERY_LENGTH, description="Query (see docs/search.md for syntax)"),
    mode: Literal["basic", "boolean", "phrase", "fuzzy"] = "basic",
    ranker: Literal["bm25", "tfidf"] = "bm25",
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(10, ge=1, le=50),
    explain: bool = False,
    record: bool = Query(True, description="Save the query in search history"),
    engine: SearchEngine = Depends(deps.engine),
    history: HistoryService = Depends(deps.history),
) -> dict:
    result = engine.search(q, mode=mode, ranker=ranker, page=page, page_size=page_size, explain=explain)
    result["history_id"] = (
        history.record(q, mode, ranker, result["total"], result["took_ms"]) if record and page == 1 and q.strip() else None
    )
    return result


@router.get("/search/suggest")
def suggest(
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(8, ge=1, le=20),
    suggester: Suggester = Depends(deps.suggester),
) -> dict:
    return {"query": q, "suggestions": suggester.suggest(q, limit)}


@router.get("/search/history")
def recent_searches(limit: int = Query(10, ge=1, le=100), history: HistoryService = Depends(deps.history)) -> dict:
    return {"recent": history.recent(limit), "popular": history.popular(limit)}


@router.delete("/search/history")
def clear_history(history: HistoryService = Depends(deps.history)) -> dict:
    return {"deleted": history.clear()}


class ClickIn(BaseModel):
    history_id: int = Field(..., ge=1)
    doc_id: int = Field(..., ge=1)


@router.post("/search/click")
def record_click(body: ClickIn, history: HistoryService = Depends(deps.history)) -> dict:
    history.record_click(body.history_id, body.doc_id)
    return {"ok": True}
