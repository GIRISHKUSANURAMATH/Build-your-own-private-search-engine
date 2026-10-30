"""Crawl endpoints. The crawler is NOT an open proxy: it only starts restricted,
rate-limited jobs (limits capped by server settings) and never returns page bodies."""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api import deps
from app.config import Settings
from app.crawler.jobs import CrawlManager
from app.database.repositories import NotFoundError
from app.utils.urls import normalize_url

router = APIRouter(tags=["crawler"])


class CrawlIn(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048, description="Seed URL (http/https)")
    max_depth: int | None = Field(None, ge=0, le=5)
    max_pages: int | None = Field(None, ge=1, le=5000)
    allowed_domains: list[str] = Field(default_factory=list, max_length=20,
                                       description="Extra domains the crawler may follow (besides the seed's host)")


def _job_dict(job, manager: CrawlManager) -> dict:
    d = job.__dict__.copy()
    d["active"] = manager.busy and job.status == "running"
    return d


@router.post("/crawl", status_code=202)
def start_crawl(body: CrawlIn, manager: CrawlManager = Depends(deps.crawl_manager),
                settings: Settings = Depends(deps.settings)) -> dict:
    seed = normalize_url(body.url)  # InvalidURLError -> 422
    manager.preflight(seed)         # BlockedURLError (private address, bad port) -> 422
    depth = min(body.max_depth if body.max_depth is not None else settings.MAX_DEPTH, settings.MAX_DEPTH)
    pages = min(body.max_pages if body.max_pages is not None else settings.MAX_PAGES, settings.MAX_PAGES)
    domains = sorted({d.strip().lower() for d in body.allowed_domains if d.strip()} | set(settings.allowed_domains_list))
    job = manager.start(seed, depth, pages, domains)
    return {"job": _job_dict(job, manager), "limits": {"max_depth": depth, "max_pages": pages,
                                                     "allowed_domains": domains,
                                                     "request_delay": settings.REQUEST_DELAY}}


@router.get("/crawl/status")
def crawl_status(job_id: int | None = Query(None, ge=1), manager: CrawlManager = Depends(deps.crawl_manager)) -> dict:
    if job_id is None:
        jobs = manager.crawl.list_jobs(limit=1)
        if not jobs:
            return {"job": None, "busy": manager.busy}
        job = jobs[0]
    else:
        job = manager.crawl.get_job(job_id)
        if job is None:
            raise NotFoundError(f"Crawl job {job_id} not found")
    return {"job": _job_dict(job, manager), "busy": manager.busy}


@router.get("/crawl/jobs")
def list_crawl_jobs(limit: int = Query(20, ge=1, le=100), manager: CrawlManager = Depends(deps.crawl_manager)) -> dict:
    return {"jobs": [_job_dict(j, manager) for j in manager.crawl.list_jobs(limit=limit)], "busy": manager.busy}


@router.post("/crawl/{job_id}/cancel")
def cancel_crawl(job_id: int, manager: CrawlManager = Depends(deps.crawl_manager)) -> dict:
    return {"job": _job_dict(manager.cancel(job_id), manager)}
