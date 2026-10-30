"""Phase 2: crawl-job life-cycle and URL crawl state."""

import pytest

from app.database.models import CrawlJobStatus, UrlStatus
from app.database.repositories import NotFoundError
from app.services.crawl_service import InvalidStatusTransition
from app.utils.urls import InvalidURLError


# ---------------------------------------------------------------- job creation
def test_create_job(crawl):
    job = crawl.create_job("HTTPS://Example.com", max_depth=2, max_pages=50)
    assert job.id == 1
    assert job.seed_url == "https://example.com/"
    assert job.status == CrawlJobStatus.PENDING
    assert (job.max_depth, job.max_pages) == (2, 50)
    assert (job.pages_discovered, job.pages_fetched, job.pages_failed) == (0, 0, 0)
    assert job.started_at is None and job.completed_at is None
    assert job.created_at.endswith("Z")


def test_create_job_rejects_invalid_seed(crawl):
    with pytest.raises(InvalidURLError):
        crawl.create_job("file:///C:/Windows/system.ini")
    assert crawl.list_jobs() == []


def test_get_and_list_jobs(crawl):
    a = crawl.create_job("https://a.com")
    b = crawl.create_job("https://b.com")
    crawl.start_job(b.id)
    assert crawl.get_job(a.id) == a
    assert crawl.get_job(999) is None
    assert [j.id for j in crawl.list_jobs()] == [b.id, a.id]  # newest first
    assert [j.id for j in crawl.list_jobs(status=CrawlJobStatus.RUNNING)] == [b.id]


# ---------------------------------------------------------------- status updates
def test_start_then_complete(crawl):
    job = crawl.create_job("https://example.com")
    running = crawl.start_job(job.id)
    assert running.status == "running" and running.started_at and running.completed_at is None
    done = crawl.complete_job(job.id)
    assert done.status == "completed"
    assert done.completed_at >= done.started_at


def test_fail_records_error(crawl):
    job = crawl.create_job("https://example.com")
    crawl.start_job(job.id)
    failed = crawl.fail_job(job.id, "DNS lookup failed")
    assert failed.status == "failed"
    assert failed.error == "DNS lookup failed"
    assert failed.completed_at is not None


def test_cancel_pending_job(crawl):
    job = crawl.create_job("https://example.com")
    cancelled = crawl.cancel_job(job.id)
    assert cancelled.status == "cancelled"
    assert cancelled.started_at is not None and cancelled.completed_at is not None


def test_cancel_running_job(crawl):
    job = crawl.create_job("https://example.com")
    crawl.start_job(job.id)
    assert crawl.cancel_job(job.id).status == "cancelled"


@pytest.mark.parametrize(
    "setup, target",
    [
        ([], "completed"),                         # can't finish without starting
        ([], "failed"),
        (["running"], "running"),                  # can't start twice
        (["running"], "pending"),                  # no going back
        (["running", "completed"], "running"),     # finished jobs are final
        (["running", "completed"], "cancelled"),
        (["cancelled"], "running"),
    ],
)
def test_invalid_transitions_are_rejected(crawl, setup, target):
    job = crawl.create_job("https://example.com")
    for status in setup:
        crawl.update_job_status(job.id, status)
    before = crawl.get_job(job.id)
    with pytest.raises(InvalidStatusTransition):
        crawl.update_job_status(job.id, target)
    assert crawl.get_job(job.id) == before  # unchanged


def test_unknown_status_rejected(crawl):
    job = crawl.create_job("https://example.com")
    with pytest.raises(ValueError):
        crawl.update_job_status(job.id, "exploded")


def test_status_update_on_missing_job(crawl):
    with pytest.raises(NotFoundError):
        crawl.start_job(999)


# ---------------------------------------------------------------- URL state
def test_discovered_url_is_deduplicated_by_normalized_form(crawl):
    job = crawl.create_job("https://example.com")
    first, new1 = crawl.record_url_discovered(job.id, "https://example.com/docs", depth=1)
    again, new2 = crawl.record_url_discovered(job.id, "HTTPS://EXAMPLE.COM:443/docs#top", depth=1)
    assert (new1, new2) == (True, False)
    assert again.id == first.id
    assert first.status == UrlStatus.PENDING and first.depth == 1 and first.crawl_job_id == job.id
    assert crawl.get_job(job.id).pages_discovered == 1  # counted once


def test_discover_for_missing_job_raises(crawl):
    with pytest.raises(NotFoundError):
        crawl.record_url_discovered(999, "https://example.com/x")


def test_record_url_state_creates_and_updates(crawl):
    rec = crawl.record_url_state("https://example.com/robots-only", UrlStatus.BLOCKED)
    assert rec.status == "blocked" and rec.attempts == 0 and rec.last_fetched_at is None

    rec2 = crawl.record_url_state(
        "https://example.com/robots-only", "failed", http_status=503, error="Service Unavailable",
        fetched=True, next_crawl_at="2030-01-01T00:00:00.000Z",
    )
    assert rec2.id == rec.id
    assert (rec2.status, rec2.http_status, rec2.last_error) == ("failed", 503, "Service Unavailable")
    assert rec2.attempts == 1 and rec2.last_fetched_at is not None
    assert rec2.next_crawl_at == "2030-01-01T00:00:00.000Z"

    rec3 = crawl.record_url_state("https://example.com/robots-only", "failed", fetched=True)
    assert rec3.attempts == 2


def test_record_url_state_rejects_unknown_status(crawl):
    with pytest.raises(ValueError):
        crawl.record_url_state("https://example.com/x", "lost")


def test_fetch_success_stores_document_url_state_and_counter(crawl, documents):
    job = crawl.create_job("https://example.com")
    crawl.start_job(job.id)
    crawl.record_url_discovered(job.id, "https://example.com/python")
    doc = crawl.record_fetch_success(
        job.id, "https://example.com/python", title="Python", content="Python text", language="en",
    )
    assert documents.get_document(doc.id).crawl_job_id == job.id

    state = crawl.get_url_state("https://example.com/python")
    assert state.status == "fetched" and state.http_status == 200
    assert state.document_id == doc.id and state.content_hash == doc.content_hash
    assert state.attempts == 1 and state.last_fetched_at is not None

    job_after = crawl.get_job(job.id)
    assert (job_after.pages_discovered, job_after.pages_fetched, job_after.pages_failed) == (1, 1, 0)


def test_refetch_updates_existing_document_instead_of_duplicating(crawl, documents):
    job = crawl.create_job("https://example.com")
    crawl.start_job(job.id)
    first = crawl.record_fetch_success(job.id, "https://example.com/p", content="v1")
    second = crawl.record_fetch_success(job.id, "https://EXAMPLE.com/p#again", content="v2")
    assert second.id == first.id
    assert second.content == "v2" and second.content_hash != first.content_hash
    assert documents.count_documents() == 1
    assert crawl.get_url_state("https://example.com/p").attempts == 2


def test_fetch_failure_updates_state_and_counter(crawl):
    job = crawl.create_job("https://example.com")
    crawl.start_job(job.id)
    rec = crawl.record_fetch_failure(job.id, "https://example.com/missing", "Not Found", http_status=404)
    assert (rec.status, rec.http_status, rec.last_error, rec.attempts) == ("failed", 404, "Not Found", 1)
    assert crawl.get_job(job.id).pages_failed == 1


def test_successful_refetch_clears_previous_error(crawl):
    job = crawl.create_job("https://example.com")
    crawl.start_job(job.id)
    crawl.record_fetch_failure(job.id, "https://example.com/flaky", "timeout")
    crawl.record_fetch_success(job.id, "https://example.com/flaky", content="ok now")
    state = crawl.get_url_state("https://example.com/flaky")
    assert state.status == "fetched" and state.last_error is None and state.attempts == 2


def test_list_pending_urls_respects_next_crawl_at(db_path, crawl):
    from app.database.database import get_connection
    from app.database.repositories import UrlRepository

    job = crawl.create_job("https://example.com")
    crawl.record_url_discovered(job.id, "https://example.com/now")
    crawl.record_url_discovered(job.id, "https://example.com/later")
    crawl.record_url_state("https://example.com/later", "pending", next_crawl_at="2999-01-01T00:00:00.000Z")
    with get_connection(db_path) as conn:
        due = UrlRepository(conn).list_by_status("pending")
        by_job = UrlRepository(conn).list_for_job(job.id)
    assert [u.normalized_url for u in due] == ["https://example.com/now"]
    assert len(by_job) == 2
