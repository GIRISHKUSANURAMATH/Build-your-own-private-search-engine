"""
CrawlManager: runs crawl jobs in a background thread, one at a time.

POST /crawl must answer immediately (a crawl with a 1-second politeness delay
takes minutes), so the job runs in a thread and the client polls
GET /crawl/status. Only one job may run at once: a single polite crawler,
and an easy guarantee that the API cannot be used to launch many crawls.

Cancelling sets the job's status to "cancelled" in the database; the crawler
checks that before every page and stops.
"""

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from app.crawler.crawler import CrawlOptions, Crawler
from app.crawler.fetcher import Fetcher
from app.crawler.security import Resolver, check_url, system_resolver
from app.database.models import CrawlJob
from app.services.crawl_service import CrawlService
from app.services.index_service import IndexService

logger = logging.getLogger("pysearch.crawler")


class CrawlBusyError(RuntimeError):
    pass


class CrawlManager:
    def __init__(self, db_path: Path, index_service: IndexService, settings,
                 fetcher_factory: Callable[[], Fetcher] | None = None, resolver: Resolver = system_resolver):
        self.db_path = db_path
        self.resolver = resolver
        self.index_service = index_service
        self.settings = settings
        self.fetcher_factory = fetcher_factory or (lambda: Fetcher(settings))
        self.crawl = CrawlService(db_path)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def preflight(self, seed_url: str) -> None:
        """Refuse a start URL the crawler would never be allowed to fetch (private
        address, bad port...) BEFORE creating a job. Raises BlockedURLError (-> 422).
        The crawler re-checks every request anyway; this just fails fast and clearly."""
        check_url(seed_url, allow_private=self.settings.ALLOW_PRIVATE_NETWORKS, resolver=self.resolver)

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, seed_url: str, max_depth: int, max_pages: int, allowed_domains: list[str],
              background: bool = True) -> CrawlJob:
        with self._lock:
            if self.busy:
                raise CrawlBusyError("A crawl is already running. Wait for it or cancel it first.")
            job = self.crawl.create_job(seed_url, max_depth=max_depth, max_pages=max_pages)
            options = CrawlOptions(max_depth, max_pages, allowed_domains)
            if background:
                self._thread = threading.Thread(target=self._run, args=(job.id, options),
                                                name=f"crawl-{job.id}", daemon=True)
                self._thread.start()
            else:
                self._run(job.id, options)
            return self.crawl.get_job(job.id) or job

    def _run(self, job_id: int, options: CrawlOptions) -> None:
        fetcher = self.fetcher_factory()
        try:
            Crawler(self.db_path, self.index_service, fetcher, self.settings).run(job_id, options)
        finally:
            fetcher.close()

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def cancel(self, job_id: int) -> CrawlJob:
        return self.crawl.cancel_job(job_id)

    def recover_interrupted(self) -> int:
        """Jobs left 'running' by a previous process (e.g. the server was stopped
        mid-crawl) can never finish; mark them failed at startup."""
        count = 0
        for job in self.crawl.list_jobs(status="running", limit=1000):
            self.crawl.fail_job(job.id, "interrupted: the server stopped while this job was running")
            count += 1
        return count
