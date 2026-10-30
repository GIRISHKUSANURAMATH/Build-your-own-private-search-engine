"""Index management and statistics endpoints."""

from fastapi import APIRouter, Depends

from app.api import deps
from app.config import Settings
from app.indexing.indexer import SearchIndex
from app.services.statistics import collection_stats, index_stats

router = APIRouter(tags=["index"])


@router.post("/index/build")
def build_index(index: SearchIndex = Depends(deps.index), settings: Settings = Depends(deps.settings)) -> dict:
    """Index documents that are new or changed since they were last indexed."""
    return {**index.index_pending(settings.DATABASE_PATH), "stats": index.stats()}


@router.post("/index/rebuild")
def rebuild_index(index: SearchIndex = Depends(deps.index), settings: Settings = Depends(deps.settings)) -> dict:
    """Throw the index away and rebuild it from every stored document."""
    return {**index.rebuild(settings.DATABASE_PATH), "stats": index.stats()}


@router.get("/index/stats")
def get_index_stats(index: SearchIndex = Depends(deps.index)) -> dict:
    return index_stats(index)


@router.get("/stats")
def get_stats(index: SearchIndex = Depends(deps.index), settings: Settings = Depends(deps.settings)) -> dict:
    return collection_stats(settings.DATABASE_PATH, index)
