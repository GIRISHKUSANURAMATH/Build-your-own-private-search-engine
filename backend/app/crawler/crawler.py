"""
The crawler: breadth-first, polite, restricted.

For each URL taken from the queue:

    robots.txt allows it? ── no ──► url state "blocked"
        │ yes
    fetch (SSRF check, rate limit, timeout, retries, size cap)
        │ error / non-2xx ──► url state "failed", job.pages_failed += 1
        │ wrong content type ──► url state "skipped"
    extract text + metadata (Phase 3)
        │ <meta robots noindex> ──► "skipped" (links may still be followed)
        │ same content as another URL (SHA-256) ──► "duplicate", not indexed
    store document + index it (IndexService), url state "fetched"
        │
    extract links ─► keep same-domain/allowed ones ─► queue at depth + 1

WHY IT CANNOT CRAWL THE WHOLE INTERNET
--------------------------------------
* Only the seed's host and explicitly ALLOWED_DOMAINS are followed.
* MAX_PAGES and MAX_DEPTH cap every job; the queue itself is capped too.
* One job at a time (CrawlManager), at least REQUEST_DELAY seconds between
  requests to a host, robots.txt obeyed, private networks refused.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from app.crawler.fetcher import Fetcher
from app.crawler.parser import extract_links
from app.crawler.robots import RobotsCache
from app.crawler.url_queue import CrawlQueue
from app.database.models import UrlStatus
from app.indexing.html_extractor import UnsupportedContentType
from app.services.crawl_service import CrawlService
from app.services.index_service import IndexService
from app.utils.urls import normalize_url

logger = logging.getLogger("pysearch.crawler")


@dataclass
class CrawlOptions:
    max_depth: int
    max_pages: int
    allowed_domains: list[str] = field(default_factory=list)


def host_allowed(url: str, seed_host: str, allowed: list[str]) -> bool:
    host = urlsplit(url).hostname or ""
    if host == seed_host:
        return True
    return any(host == d or host.endswith("." + d) for d in allowed)


class Crawler:
    def __init__(self, db_path: Path, index_service: IndexService, fetcher: Fetcher, settings):
        self.db_path = db_path
        self.index_service = index_service
        self.fetcher = fetcher
        self.settings = settings
        self.crawl = CrawlService(db_path)
        self.robots = RobotsCache(fetcher, settings.USER_AGENT)
        if settings.RESPECT_ROBOTS_TXT:
            fetcher.robots_delay = self.robots.crawl_delay

    def run(self, job_id: int, options: CrawlOptions) -> dict:
        job = self.crawl.get_job(job_id)
        if job is None:
            raise ValueError(f"crawl job {job_id} not found")
        if job.status == "pending":
            self.crawl.start_job(job_id)
        seed = normalize_url(job.seed_url)
        seed_host = urlsplit(seed).hostname or ""
        queue = CrawlQueue(options.max_depth, max_urls=options.max_pages * 20)
        queue.push(seed, 0)
        self.crawl.record_url_queued(job_id, seed, 0)
        processed = 0
        summary = {"fetched": 0, "failed": 0, "skipped": 0, "duplicates": 0, "blocked": 0}

        try:
            while (item := queue.pop()) is not None and processed < options.max_pages:
                if self.crawl.is_cancelled(job_id):
                    logger.info("crawl job %s cancelled", job_id)
                    return summary
                processed += 1
                outcome = self._process(job_id, item.url, item.depth, options, queue, seed_host)
                summary[outcome] += 1
            self.crawl.complete_job(job_id)
        except Exception as exc:  # unexpected bug: record it on the job instead of dying silently
            logger.exception("crawl job %s failed", job_id)
            if not self.crawl.is_cancelled(job_id):
                self.crawl.fail_job(job_id, f"{type(exc).__name__}: {exc}")
        return summary

    def _process(self, job_id: int, url: str, depth: int, options: CrawlOptions,
                 queue: CrawlQueue, seed_host: str) -> str:
        if self.settings.RESPECT_ROBOTS_TXT and not self.robots.allowed(url):
            self.crawl.record_url_state(url, UrlStatus.BLOCKED, crawl_job_id=job_id, error="disallowed by robots.txt")
            return "blocked"

        result = self.fetcher.fetch(url)
        if result.error and result.error.startswith("blocked"):
            self.crawl.record_url_state(url, UrlStatus.BLOCKED, crawl_job_id=job_id, error=result.error)
            return "blocked"
        if result.error and result.error.startswith("skipped"):
            self.crawl.record_url_state(url, UrlStatus.SKIPPED, crawl_job_id=job_id,
                                        http_status=result.status, error=result.error, fetched=True)
            return "skipped"
        if not result.ok:
            reason = result.error or f"HTTP {result.status}"
            self.crawl.record_fetch_failure(job_id, url, reason, http_status=result.status)
            return "failed"

        final_url = result.url
        if final_url != url and not host_allowed(final_url, seed_host, options.allowed_domains):
            self.crawl.record_url_state(url, UrlStatus.SKIPPED, crawl_job_id=job_id, http_status=result.status,
                                        error=f"redirected off-site to {final_url}", fetched=True)
            return "skipped"

        is_html = result.content_type in ("text/html", "application/xhtml+xml")
        page = extract_links(result.body, final_url) if is_html else None
        outcome = "fetched"

        if page is not None and page.noindex:
            self.crawl.record_url_state(url, UrlStatus.SKIPPED, crawl_job_id=job_id, http_status=result.status,
                                        error="meta robots noindex", fetched=True)
            outcome = "skipped"
        else:
            # A same-site <link rel=canonical> names the page's preferred URL.
            doc_url = final_url
            if page is not None and page.canonical and host_allowed(page.canonical, seed_host, options.allowed_domains):
                doc_url = page.canonical
            try:
                ingest = self.index_service.ingest_raw(
                    doc_url, result.body, result.content_type, http_status=result.status,
                    crawl_job_id=job_id, allow_duplicate_content=False,
                )
            except UnsupportedContentType:
                self.crawl.record_url_state(url, UrlStatus.SKIPPED, crawl_job_id=job_id, fetched=True,
                                            error=f"unsupported content type {result.content_type}")
                return "skipped"
            if ingest.duplicate_of is not None:
                self.crawl.record_url_state(
                    url, UrlStatus.DUPLICATE, crawl_job_id=job_id, http_status=result.status, fetched=True,
                    document_id=ingest.duplicate_of, content_hash=ingest.document.content_hash,
                    error=f"same content as document {ingest.duplicate_of}",
                )
                outcome = "duplicates"
            else:
                self.crawl.record_url_state(
                    url, UrlStatus.FETCHED, crawl_job_id=job_id, http_status=result.status, fetched=True,
                    document_id=ingest.document.id, content_hash=ingest.document.content_hash,
                )
                self.crawl.record_fetch_counted(job_id)

        if page is not None and depth < options.max_depth:
            for link in page.links:
                if host_allowed(link, seed_host, options.allowed_domains) and queue.push(link, depth + 1):
                    self.crawl.record_url_queued(job_id, link, depth + 1)
        return outcome
