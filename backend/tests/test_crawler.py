"""Phase 9: crawler — security, parsing, robots.txt, fetching, and full crawls."""

import httpx2 as httpx
import pytest

from app.config import Settings
from app.crawler.crawler import CrawlOptions, host_allowed
from app.crawler.fetcher import Fetcher, RateLimiter
from app.crawler.jobs import CrawlBusyError, CrawlManager
from app.crawler.parser import extract_links
from app.crawler.robots import RobotsCache
from app.crawler.security import BlockedURLError, check_url
from app.crawler.url_queue import CrawlQueue
from app.database.database import get_connection
from tests.conftest import make_site_handler

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731


# ---------------------------------------------------------------- SSRF guard
@pytest.mark.parametrize(
    "url, addrs",
    [
        ("http://127.0.0.1/", None),
        ("http://localhost:8080/", None),
        ("http://admin.localhost/", None),
        ("http://192.168.1.1/", None),
        ("http://10.0.0.5/", None),
        ("http://169.254.169.254/latest/meta-data/", None),
        ("http://[::1]/", None),
        ("http://0.0.0.0/", None),
        ("http://evil.example.com/", ["10.1.2.3"]),          # public name, private address
        ("http://mixed.example.com/", ["93.184.216.34", "127.0.0.1"]),
        ("ftp://example.com/", ["93.184.216.34"]),
        ("http://example.com:22/", ["93.184.216.34"]),        # SSH port
        ("http:///nohost", None),
    ],
)
def test_ssrf_blocked(url, addrs):
    with pytest.raises(BlockedURLError):
        check_url(url, resolver=lambda h: addrs or [])


def test_public_url_allowed():
    check_url("https://example.com/page", resolver=PUBLIC)
    check_url("http://example.com:8080/", resolver=PUBLIC)


def test_resolution_failure_blocks():
    def fail(host):
        raise OSError("no such host")
    with pytest.raises(BlockedURLError, match="cannot resolve"):
        check_url("https://nonexistent.example/", resolver=fail)


def test_allow_private_networks_opt_in():
    check_url("http://127.0.0.1:8765/", allow_private=True)


# ---------------------------------------------------------------- link extraction
def test_extract_links_resolves_normalizes_and_filters():
    html = """<html><head><base href="https://site.example.com/docs/"></head><body>
      <a href="intro.html">rel</a> <a href="/about#team">abs</a> <a href="https://SITE.example.com/about">dup</a>
      <a href="mailto:a@b.c">mail</a> <a href="javascript:void(0)">js</a> <a href="#top">frag</a>
      <a href="tel:123">tel</a> <a href="secret.html" rel="nofollow">nf</a> <a href="">empty</a>
      <a href="http://[bad">broken</a></body></html>"""
    page = extract_links(html, "https://site.example.com/index.html")
    assert page.links == ["https://site.example.com/docs/intro.html", "https://site.example.com/about"]
    assert not page.noindex and not page.nofollow


def test_meta_robots():
    page = extract_links('<meta name="robots" content="noindex, nofollow"><a href="/x">x</a>', "https://s.example.com/")
    assert page.noindex and page.nofollow and page.links == []
    assert extract_links('<meta name="robots" content="none"><a href="/x">x</a>', "https://s.example.com/").noindex


def test_canonical_link():
    page = extract_links('<link rel="canonical" href="/Page?b=2&a=1#x">', "https://s.example.com/page?a=1&b=2")
    assert page.canonical == "https://s.example.com/Page?a=1&b=2"


# ---------------------------------------------------------------- queue / domains
def test_queue_bfs_dedup_depth_cap():
    q = CrawlQueue(max_depth=1, max_urls=3)
    assert q.push("https://a/", 0) and not q.push("https://a/", 0)
    assert q.push("https://a/1", 1) and not q.push("https://a/deep", 2)
    assert q.push("https://a/2", 1) and not q.push("https://a/3", 1)   # cap reached
    assert [q.pop().url for _ in range(3)] == ["https://a/", "https://a/1", "https://a/2"]
    assert q.pop() is None


def test_host_allowed():
    assert host_allowed("https://site.example.com/x", "site.example.com", [])
    assert not host_allowed("https://other.example.com/", "site.example.com", [])
    assert host_allowed("https://docs.python.org/", "site.example.com", ["python.org"])
    assert not host_allowed("https://evilpython.org/", "site.example.com", ["python.org"])


# ---------------------------------------------------------------- robots.txt
def _robots(make_fetcher, status, body="", ctype="text/plain"):
    handler = make_site_handler({"/robots.txt": (status, ctype, body)})
    return RobotsCache(make_fetcher(handler), "PySearchBot")


def test_robots_rules_and_crawl_delay(make_fetcher):
    r = _robots(make_fetcher, 200, "User-agent: *\nDisallow: /private/\nCrawl-delay: 3\n")
    assert r.allowed("https://site.example.com/public")
    assert not r.allowed("https://site.example.com/private/x")
    assert r.crawl_delay("https://site.example.com/") == 3.0


def test_robots_missing_allows_all(make_fetcher):
    assert _robots(make_fetcher, 404).allowed("https://site.example.com/anything")


def test_robots_server_error_disallows_all(make_fetcher, settings):
    s = settings.model_copy(update={"MAX_RETRIES": 0})
    handler = make_site_handler({"/robots.txt": (503, "text/plain", "down")})
    assert not RobotsCache(make_fetcher(handler, s=s), "PySearchBot").allowed("https://site.example.com/")


def test_robots_fetched_once_per_site(make_fetcher):
    calls = []
    handler = make_site_handler({"/robots.txt": (200, "text/plain", "User-agent: *\nDisallow:\n")}, calls)
    r = RobotsCache(make_fetcher(handler), "PySearchBot")
    for p in ("/a", "/b", "/c"):
        r.allowed("https://site.example.com" + p)
    assert calls.count("/robots.txt") == 1


# ---------------------------------------------------------------- fetcher
def test_fetch_ok(make_fetcher):
    f = make_fetcher(make_site_handler({"/": (200, "text/html; charset=utf-8", "<p>hi</p>")}))
    r = f.fetch("https://site.example.com/")
    assert r.ok and r.status == 200 and r.content_type == "text/html" and r.text == "<p>hi</p>"


def test_fetch_skips_wrong_content_type(make_fetcher):
    f = make_fetcher(make_site_handler({"/a.pdf": (200, "application/pdf", b"%PDF")}))
    r = f.fetch("https://site.example.com/a.pdf")
    assert not r.ok and r.error.startswith("skipped") and r.body == b""


def test_fetch_size_limit(make_fetcher, settings):
    s = settings.model_copy(update={"MAX_CONTENT_BYTES": 10_000})
    f = make_fetcher(make_site_handler({"/big": (200, "text/html", "x" * 50_000)}), s=s)
    r = f.fetch("https://site.example.com/big")
    assert r.truncated and len(r.body) == 10_000


def test_redirect_followed(make_fetcher):
    site = {"/old": (301, "text/html", "", {"location": "/new"}), "/new": (200, "text/html", "<p>new</p>")}
    r = make_fetcher(make_site_handler(site)).fetch("https://site.example.com/old")
    assert r.ok and r.url == "https://site.example.com/new" and r.redirects == ["https://site.example.com/new"]


def test_redirect_to_private_address_blocked(make_fetcher):
    site = {"/go": (302, "text/html", "", {"location": "http://169.254.169.254/latest/meta-data/"})}
    r = make_fetcher(make_site_handler(site)).fetch("https://site.example.com/go")
    assert not r.ok and r.error.startswith("blocked")


def test_redirect_loop_stops(make_fetcher):
    site = {"/a": (302, "text/html", "", {"location": "/b"}), "/b": (302, "text/html", "", {"location": "/a"})}
    r = make_fetcher(make_site_handler(site)).fetch("https://site.example.com/a")
    assert "too many redirects" in r.error


def test_retries_transient_errors_then_succeeds(make_fetcher, settings):
    attempts = []

    def flaky(request):
        attempts.append(1)
        return httpx.Response(503 if len(attempts) < 3 else 200, headers={"content-type": "text/html"}, text="ok")
    sleeps = []
    f = make_fetcher(make_site_handler({"/": (0, "", flaky)}), sleeps=sleeps,
                     s=settings.model_copy(update={"MAX_RETRIES": 2}))
    r = f.fetch("https://site.example.com/")
    assert r.ok and len(attempts) == 3
    assert 0.5 in sleeps and 1.0 in sleeps   # exponential backoff (other entries are rate-limit waits)


def test_no_retry_on_404(make_fetcher):
    calls = []
    r = make_fetcher(make_site_handler({}, calls)).fetch("https://site.example.com/missing")
    assert r.status == 404 and calls == ["/missing"]


def test_timeout_reported(make_fetcher, settings):
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)
    f = make_fetcher(make_site_handler({"/": (0, "", slow)}), s=settings.model_copy(update={"MAX_RETRIES": 1}))
    r = f.fetch("https://site.example.com/")
    assert r.error == "timeout" and not r.ok


def test_rate_limiter_waits_between_requests_to_same_host():
    now = [100.0]
    slept = []

    def sleep(s):
        slept.append(s)
        now[0] += s
    rl = RateLimiter(1.0, clock=lambda: now[0], sleep=sleep)
    rl.wait("a.com")
    rl.wait("b.com")               # different host: no wait
    now[0] += 0.3
    rl.wait("a.com")               # 0.3s after the last a.com request -> wait 0.7s
    rl.wait("a.com", delay=2.0)    # robots Crawl-delay raises the delay
    assert slept == [pytest.approx(0.7), pytest.approx(2.0)]


# ---------------------------------------------------------------- full crawl
SITE = {
    "/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /private/\n"),
    "/": (200, "text/html",
          '<title>Home</title><nav><a href="/python">Py</a> <a href="/db">DB</a> <a href="/private/x">s</a> '
          '<a href="https://other.example.org/">off</a> <a href="/missing">404</a> <a href="/img.png">img</a> '
          '<a href="/old">old</a> <a href="/noindex">ni</a> <a href="/copy">copy</a> <a href="http://127.0.0.1/">lo</a>'
          '</nav><main><p>Welcome to the programming site.</p></main>'),
    "/python": (200, "text/html", '<title>Python</title><main><p>Python is a language.</p><a href="/python/deep">d</a></main>'),
    "/python/deep": (200, "text/html", "<title>Deep</title><p>deep page</p>"),
    "/db": (200, "text/html", "<title>Databases</title><main><p>SQLite stores data in one file.</p></main>"),
    "/copy": (200, "text/html", "<title>Copy</title><main><p>SQLite stores data in one file.</p></main>"),
    "/img.png": (200, "image/png", b"\x89PNG"),
    "/old": (301, "text/html", "", {"location": "/db"}),
    "/noindex": (200, "text/html", '<meta name="robots" content="noindex"><p>hidden zebra</p>'),
    "/private/x": (200, "text/html", "<p>never</p>"),
}


@pytest.fixture
def manager(db_path, index_service, settings, make_fetcher):
    calls: list[str] = []
    m = CrawlManager(db_path, index_service, settings,
                     fetcher_factory=lambda: make_fetcher(make_site_handler(SITE, calls)))
    m.calls = calls
    return m


def url_states(db_path):
    with get_connection(db_path) as conn:
        return {r["normalized_url"].replace("https://site.example.com", ""): r["status"]
                for r in conn.execute("SELECT normalized_url, status FROM urls")}


def test_full_crawl(manager, db_path, search_index):
    job = manager.start("https://site.example.com/", 1, 50, [], background=False)
    job = manager.crawl.get_job(job.id)
    assert job.status == "completed" and job.error is None
    states = url_states(db_path)
    assert states == {
        "/": "fetched", "/python": "fetched", "/db": "fetched", "/private/x": "blocked",
        "/missing": "failed", "/img.png": "skipped", "/old": "fetched", "/noindex": "skipped",
        "/copy": "duplicate",
    }
    assert "/private/x" not in manager.calls                   # robots.txt obeyed: never requested
    assert "/python/deep" not in states                        # depth limit
    assert not any("other.example.org" in u or "127.0.0.1" in u for u in states)  # off-site never queued
    assert job.pages_failed == 1 and job.pages_discovered == 9
    assert search_index.doc_count == 3                         # home, python, db (no copy, no noindex)
    assert search_index.body.docs("zebra") == set()


def test_crawl_respects_max_pages(manager, db_path):
    job = manager.start("https://site.example.com/", 2, 2, [], background=False)
    assert len([u for u in manager.calls if u != "/robots.txt"]) == 2
    assert manager.crawl.get_job(job.id).status == "completed"


def test_crawl_depth_zero_only_seed(manager, db_path):
    manager.start("https://site.example.com/", 0, 50, [], background=False)
    assert set(url_states(db_path)) == {"/"}


def test_recrawl_updates_instead_of_duplicating(manager, db_path, search_index):
    manager.start("https://site.example.com/", 1, 50, [], background=False)
    before = search_index.doc_count
    job2 = manager.start("https://site.example.com/", 1, 50, [], background=False)
    assert search_index.doc_count == before
    assert manager.crawl.get_job(job2.id).pages_discovered == 9   # counted per job


def test_cancelled_job_stops(manager, db_path, monkeypatch):
    from app.services.crawl_service import CrawlService

    original = CrawlService.is_cancelled
    calls = {"n": 0}

    def cancel_after_two(self, job_id):
        calls["n"] += 1
        if calls["n"] == 3:
            self.cancel_job(job_id)
        return original(self, job_id)
    monkeypatch.setattr(CrawlService, "is_cancelled", cancel_after_two)
    job = manager.start("https://site.example.com/", 1, 50, [], background=False)
    job = manager.crawl.get_job(job.id)
    assert job.status == "cancelled" and job.pages_fetched == 2


def test_unexpected_error_marks_job_failed(manager, monkeypatch):
    from app.crawler import crawler as crawler_mod

    def boom(*a, **kw):
        raise RuntimeError("parser exploded")
    monkeypatch.setattr(crawler_mod, "extract_links", boom)
    job = manager.start("https://site.example.com/", 1, 5, [], background=False)
    job = manager.crawl.get_job(job.id)
    assert job.status == "failed" and "parser exploded" in job.error


def test_background_crawl_and_busy_guard(db_path, index_service, settings, make_fetcher):
    import threading

    release = threading.Event()

    def slow_home(request):
        release.wait(5)
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>slow</p>")
    site = {"/robots.txt": (404, "text/plain", ""), "/": (0, "", slow_home)}
    m = CrawlManager(db_path, index_service, settings, fetcher_factory=lambda: make_fetcher(make_site_handler(site)))
    job = m.start("https://site.example.com/", 0, 1, [])
    assert m.busy
    with pytest.raises(CrawlBusyError):
        m.start("https://site.example.com/", 0, 1, [])
    release.set()
    m.wait(5)
    assert not m.busy and m.crawl.get_job(job.id).status == "completed"


def test_recover_interrupted_jobs(db_path, index_service, settings):
    m = CrawlManager(db_path, index_service, settings)
    job = m.crawl.create_job("https://site.example.com/")
    m.crawl.start_job(job.id)
    assert m.recover_interrupted() == 1
    assert m.crawl.get_job(job.id).status == "failed"


def test_crawler_blocks_private_seed_by_default(db_path, index_service, tmp_path):
    s = Settings(DATABASE_PATH=tmp_path / "x.db", ENVIRONMENT="test", MAX_RETRIES=0)
    real = Fetcher(s, client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
                   sleep=lambda _: None)
    m = CrawlManager(db_path, index_service, s, fetcher_factory=lambda: real)
    job = m.start("http://127.0.0.1:8765/", 0, 1, [], background=False)
    assert url_states(db_path) == {"http://127.0.0.1:8765/": "blocked"}
    assert m.crawl.get_job(job.id).status == "completed"


def test_crawl_options_dataclass():
    o = CrawlOptions(1, 10)
    assert o.allowed_domains == []
