"""
Fetcher: download one URL safely and politely.

* SSRF check before the request AND before every redirect hop (redirects are
  followed manually, max 5) — otherwise a public page could redirect us to
  http://127.0.0.1/.
* Rate limiting: at least REQUEST_DELAY seconds between requests to the same
  host (more if robots.txt asks via Crawl-delay).
* Timeouts on connect and read.
* Retries with exponential backoff for temporary failures only:
  timeouts, connection errors, 429 Too Many Requests, 5xx server errors.
  4xx errors (404 Not Found...) are not retried: they will not fix themselves.
* Content-type filtering: only text/html and text/plain bodies are downloaded.
* Size limit: the body is streamed and we stop at MAX_CONTENT_BYTES.

The HTTP client, DNS resolver, clock and sleep function are injectable, so
the tests run against a fake website with no network and no real waiting.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx2 as httpx

from app.crawler.security import BlockedURLError, Resolver, check_url, system_resolver
from app.utils.urls import InvalidURLError, normalize_url

ACCEPTED_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_REDIRECTS = 5


@dataclass
class FetchResult:
    url: str                       # final URL after redirects
    status: int | None = None
    content_type: str = ""
    body: bytes = b""
    error: str | None = None
    redirects: list[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and self.status is not None and 200 <= self.status < 300

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class RateLimiter:
    """Minimum delay between two requests to the same host."""

    def __init__(self, delay: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.delay = delay
        self.clock, self.sleep = clock, sleep
        self._last: dict[str, float] = {}

    def wait(self, host: str, delay: float | None = None) -> float:
        needed = max(self.delay, delay or 0)
        last = self._last.get(host)
        waited = 0.0
        if last is not None:
            remaining = needed - (self.clock() - last)
            if remaining > 0:
                self.sleep(remaining)
                waited = remaining
        self._last[host] = self.clock()
        return waited


class Fetcher:
    def __init__(self, settings, client: httpx.Client | None = None, resolver: Resolver = system_resolver,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.settings = settings
        self.resolver = resolver
        self.sleep = sleep
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(settings.REQUEST_TIMEOUT),
            headers={"User-Agent": settings.USER_AGENT, "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1"},
            follow_redirects=False,
        )
        self.rate_limiter = RateLimiter(settings.REQUEST_DELAY, clock=clock, sleep=sleep)
        self.robots_delay: Callable[[str], float | None] = lambda url: None

    def close(self) -> None:
        self.client.close()

    def _check(self, url: str) -> None:
        check_url(url, allow_private=self.settings.ALLOW_PRIVATE_NETWORKS, resolver=self.resolver)

    def fetch(self, url: str, accept_types: tuple[str, ...] | None = ACCEPTED_TYPES,
              follow_robots: bool = True) -> FetchResult:
        """Fetch with retries. accept_types=None accepts any content type (robots.txt)."""
        attempts = self.settings.MAX_RETRIES + 1
        result = FetchResult(url)
        for attempt in range(attempts):
            result = self._fetch_once(url, accept_types, follow_robots)
            transient_error = result.error == "timeout" or (result.error or "").startswith("network error")
            retryable = transient_error or result.status in RETRY_STATUS
            if not retryable or attempt == attempts - 1:
                break
            self.sleep(0.5 * (2 ** attempt))   # 0.5s, 1s, 2s ...
        return result

    def _fetch_once(self, url: str, accept_types, follow_robots: bool) -> FetchResult:
        current = url
        redirects: list[str] = []
        for _ in range(MAX_REDIRECTS + 1):
            try:
                self._check(current)
            except BlockedURLError as exc:
                return FetchResult(current, error=f"blocked: {exc}", redirects=redirects)
            host = urlsplit(current).netloc
            self.rate_limiter.wait(host, self.robots_delay(current) if follow_robots else None)
            try:
                with self.client.stream("GET", current) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308) and "location" in resp.headers:
                        try:
                            nxt = normalize_url(urljoin(current, resp.headers["location"]))
                        except InvalidURLError as exc:
                            return FetchResult(current, resp.status_code, error=f"bad redirect: {exc}")
                        redirects.append(nxt)
                        current = nxt
                        continue
                    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    result = FetchResult(current, resp.status_code, ctype, redirects=redirects)
                    if not 200 <= resp.status_code < 300:
                        return result
                    if accept_types is not None and ctype not in accept_types:
                        result.error = f"skipped: content type {ctype or 'unknown'}"
                        return result
                    chunks, size = [], 0
                    for chunk in resp.iter_bytes():
                        size += len(chunk)
                        if size > self.settings.MAX_CONTENT_BYTES:
                            chunks.append(chunk[: len(chunk) - (size - self.settings.MAX_CONTENT_BYTES)])
                            result.truncated = True
                            break
                        chunks.append(chunk)
                    result.body = b"".join(chunks)
                    return result
            except httpx.TimeoutException:
                return FetchResult(current, error="timeout", redirects=redirects)
            except httpx.HTTPError as exc:
                return FetchResult(current, error=f"network error: {type(exc).__name__}: {exc}", redirects=redirects)
        return FetchResult(current, error=f"too many redirects (>{MAX_REDIRECTS})", redirects=redirects)
