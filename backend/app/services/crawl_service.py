"""
Crawl-job and URL-state service.

Phase 2 provides the STORAGE side of crawling only: creating jobs, moving
them through their life-cycle, and recording what happened to each URL.
The crawler that actually downloads pages arrives in Phase 9 and will call
these methods.

Job life-cycle (enforced here, not just by the CHECK constraint):

    pending ──► running ──► completed
       │           ├──────► failed
       └───────────┴──────► cancelled
"""

from pathlib import Path

from app.database.database import get_connection
from app.database.models import CrawlJob, CrawlJobStatus, Document, UrlRecord, UrlStatus
from app.database.repositories import CrawlJobRepository, DocumentRepository, UrlRepository
from app.services.document_service import compute_content_hash
from app.utils.urls import get_domain, normalize_url

S = CrawlJobStatus
ALLOWED_TRANSITIONS: dict[CrawlJobStatus, set[CrawlJobStatus]] = {
    S.PENDING: {S.RUNNING, S.CANCELLED},
    S.RUNNING: {S.COMPLETED, S.FAILED, S.CANCELLED},
    S.COMPLETED: set(),
    S.FAILED: set(),
    S.CANCELLED: set(),
}
FINISHED = {S.COMPLETED, S.FAILED, S.CANCELLED}


class InvalidStatusTransition(ValueError):
    pass


class CrawlService:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    # ------------------------------------------------------------- jobs
    def create_job(self, seed_url: str, max_depth: int | None = None, max_pages: int | None = None) -> CrawlJob:
        """Create a pending job. The seed URL is validated and stored normalized."""
        seed = normalize_url(seed_url)
        with get_connection(self.db_path) as conn:
            return CrawlJobRepository(conn).insert(seed, max_depth=max_depth, max_pages=max_pages)

    def get_job(self, job_id: int) -> CrawlJob | None:
        with get_connection(self.db_path) as conn:
            return CrawlJobRepository(conn).get(job_id)

    def list_jobs(self, status: CrawlJobStatus | None = None, limit: int = 50) -> list[CrawlJob]:
        with get_connection(self.db_path) as conn:
            return CrawlJobRepository(conn).list(status=status, limit=limit)

    def update_job_status(self, job_id: int, new_status: CrawlJobStatus | str, error: str | None = None) -> CrawlJob:
        new = CrawlJobStatus(new_status)  # ValueError for unknown strings
        with get_connection(self.db_path) as conn:
            repo = CrawlJobRepository(conn)
            current = CrawlJobStatus(repo.get_or_raise(job_id).status)
            if new not in ALLOWED_TRANSITIONS[current]:
                raise InvalidStatusTransition(f"Crawl job {job_id}: cannot go from {current} to {new}")
            # A job cancelled while still pending never ran, but the table's
            # CHECK requires started_at whenever completed_at is set, so we
            # stamp both (duration = 0).
            cancelled_before_start = current == S.PENDING and new == S.CANCELLED
            return repo.set_status(
                job_id,
                new,
                mark_started=(new == S.RUNNING) or cancelled_before_start,
                mark_completed=new in FINISHED,
                error=error,
            )

    def start_job(self, job_id: int) -> CrawlJob:
        return self.update_job_status(job_id, S.RUNNING)

    def complete_job(self, job_id: int) -> CrawlJob:
        return self.update_job_status(job_id, S.COMPLETED)

    def fail_job(self, job_id: int, error: str) -> CrawlJob:
        return self.update_job_status(job_id, S.FAILED, error=error)

    def cancel_job(self, job_id: int) -> CrawlJob:
        return self.update_job_status(job_id, S.CANCELLED)

    # ------------------------------------------------------------- URL state
    def record_url_discovered(self, job_id: int, url: str, depth: int = 0) -> tuple[UrlRecord, bool]:
        """Register a URL found by the crawler. Returns (record, is_new).

        Only genuinely new URLs bump the job's pages_discovered counter — both
        writes happen in one transaction so the counter can't drift.
        """
        normalized = normalize_url(url)
        with get_connection(self.db_path) as conn:
            jobs = CrawlJobRepository(conn)
            jobs.get_or_raise(job_id)
            record, created = UrlRepository(conn).add_if_new(
                url, normalized, get_domain(normalized), depth=depth, crawl_job_id=job_id
            )
            if created:
                jobs.increment_counters(job_id, discovered=1)
            return record, created

    def record_url_queued(self, job_id: int, url: str, depth: int) -> UrlRecord:
        """Phase 9: the crawler queued this URL in this job.

        Unlike record_url_discovered (counts a URL only the first time it is
        ever seen), this counts per JOB: re-crawling a site in a new job
        reports its pages as discovered again. The URL row is (re)assigned to
        this job and marked pending.
        """
        normalized = normalize_url(url)
        with get_connection(self.db_path) as conn:
            jobs = CrawlJobRepository(conn)
            jobs.get_or_raise(job_id)
            record = UrlRepository(conn).upsert_state(
                url, normalized, get_domain(normalized),
                status=UrlStatus.PENDING, depth=depth, crawl_job_id=job_id,
            )
            jobs.increment_counters(job_id, discovered=1)
            return record

    def record_fetch_counted(self, job_id: int) -> None:
        """Count one successfully stored page for the job (the crawler stores the
        document through IndexService, which also indexes it)."""
        with get_connection(self.db_path) as conn:
            CrawlJobRepository(conn).increment_counters(job_id, fetched=1)

    def is_cancelled(self, job_id: int) -> bool:
        job = self.get_job(job_id)
        return job is None or job.status == CrawlJobStatus.CANCELLED

    def record_url_state(
        self,
        url: str,
        status: UrlStatus | str,
        *,
        crawl_job_id: int | None = None,
        http_status: int | None = None,
        error: str | None = None,
        content_hash: str | None = None,
        document_id: int | None = None,
        next_crawl_at: str | None = None,
        fetched: bool = False,
    ) -> UrlRecord:
        """Record what happened to a URL (any status). Creates the row if unseen.

        `fetched=True` means a network request was made: attempts += 1 and
        last_fetched_at = now.
        """
        normalized = normalize_url(url)
        state: dict = {"status": UrlStatus(status)}
        optional = {
            "crawl_job_id": crawl_job_id, "http_status": http_status, "last_error": error,
            "content_hash": content_hash, "document_id": document_id, "next_crawl_at": next_crawl_at,
        }
        state.update({k: v for k, v in optional.items() if v is not None})
        with get_connection(self.db_path) as conn:
            if fetched:
                state["last_fetched_at"] = conn.execute(
                    "SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
                ).fetchone()[0]
            return UrlRepository(conn).upsert_state(
                url, normalized, get_domain(normalized), count_attempt=fetched, **state
            )

    def get_url_state(self, url: str) -> UrlRecord | None:
        with get_connection(self.db_path) as conn:
            return UrlRepository(conn).get(normalize_url(url))

    # ------------------------------------------------------------- multi-table writes
    def record_fetch_success(
        self,
        job_id: int,
        url: str,
        *,
        title: str = "",
        content: str = "",
        http_status: int = 200,
        content_type: str = "text/html",
        author: str | None = None,
        language: str | None = None,
    ) -> Document:
        """Store a fetched page: document upsert + URL state + job counter, in ONE transaction.

        If any step fails, none of the three changes are kept.
        Re-fetching an existing canonical URL updates its document instead of
        creating a second one.
        """
        canonical = normalize_url(url)
        domain = get_domain(canonical)
        content_hash = compute_content_hash(content)
        fields = dict(
            url=url.strip(), title=title, content=content, author=author, domain=domain,
            content_type=content_type, language=language, http_status=http_status,
            content_hash=content_hash, crawl_job_id=job_id,
        )
        with get_connection(self.db_path) as conn:
            jobs, docs, urls = CrawlJobRepository(conn), DocumentRepository(conn), UrlRepository(conn)
            jobs.get_or_raise(job_id)

            existing = docs.get_by_canonical_url(canonical)
            doc = docs.update(existing.id, **fields) if existing else docs.insert(canonical_url=canonical, **fields)

            now = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0]
            urls.upsert_state(
                url, canonical, domain, count_attempt=True,
                status=UrlStatus.FETCHED, http_status=http_status, content_hash=content_hash,
                document_id=doc.id, crawl_job_id=job_id, last_fetched_at=now, last_error=None,
            )
            jobs.increment_counters(job_id, fetched=1)
            return doc

    def record_fetch_failure(self, job_id: int, url: str, error: str, http_status: int | None = None) -> UrlRecord:
        """Mark a URL as failed and bump the job's failure counter, in one transaction."""
        canonical = normalize_url(url)
        with get_connection(self.db_path) as conn:
            jobs = CrawlJobRepository(conn)
            jobs.get_or_raise(job_id)
            now = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0]
            state = dict(status=UrlStatus.FAILED, last_error=error, crawl_job_id=job_id, last_fetched_at=now)
            if http_status is not None:
                state["http_status"] = http_status
            record = UrlRepository(conn).upsert_state(
                url, canonical, get_domain(canonical), count_attempt=True, **state
            )
            jobs.increment_counters(job_id, failed=1)
            return record
