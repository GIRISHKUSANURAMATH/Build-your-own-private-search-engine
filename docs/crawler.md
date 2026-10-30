# Crawler

Code: `backend/app/crawler/`. Start a crawl from the admin page,
`POST /crawl`, or `python manage.py crawl --url https://example.com --depth 2`.

## What happens to each URL

```
queue (breadth-first) ─► robots.txt allows it?  ── no ──► "blocked"
                              │ yes
                         SSRF check ─► rate limit ─► GET (timeout, retries, size cap, redirects re-checked)
                              │ network error / non-2xx ──► "failed"  (job.pages_failed += 1)
                              │ not HTML/text            ──► "skipped"
                         extract text + metadata (Phase 3), links, <meta robots>
                              │ noindex                  ──► "skipped" (links still followed)
                              │ same SHA-256 as another URL ──► "duplicate" (not indexed)
                         store + index document ──► "fetched"
                              │
                         same-site links ─► queue at depth + 1 (if depth < max_depth)
```

Each URL's outcome is stored in the `urls` table (status, HTTP status,
attempts, last error, document id). Counters are kept on the `crawl_jobs` row.

## Why it cannot crawl the whole internet

| Limit | Setting | Default |
|-------|---------|---------|
| Same site only: the seed's host plus explicitly allowed domains | `ALLOWED_DOMAINS` | seed host only |
| Pages per job | `MAX_PAGES` | 50 |
| Link depth from the seed | `MAX_DEPTH` | 2 |
| Delay between requests to the same host | `REQUEST_DELAY` (or robots.txt `Crawl-delay`, if larger) | 1.0 s |
| Request timeout | `REQUEST_TIMEOUT` | 10 s |
| Retries for timeouts / 429 / 5xx (backoff 0.5 s, 1 s, 2 s...) | `MAX_RETRIES` | 2 |
| Maximum page size | `MAX_CONTENT_BYTES` | 5 MB |
| Jobs at once | — | 1 |

API requests can ask for lower limits but never higher than the settings.

## Politeness

- **robots.txt (RFC 9309):** fetched once per site and parsed with
  `urllib.robotparser`. A 4xx response means there are no rules, so
  everything is allowed; a 5xx or network error means "disallow everything"
  for now. `Crawl-delay` is honoured.
- **Pages:** `<meta name="robots" content="noindex">` pages are not stored,
  `nofollow` pages and `rel="nofollow"` links are not followed, and requests
  identify themselves with a clear User-Agent.

## Security: SSRF

Because the server fetches URLs it is given, it could be abused to reach
things only the server can reach: `http://127.0.0.1/`, a home router at
`192.168.1.1`, or cloud metadata at `169.254.169.254`. Before **every**
request, including every redirect hop:

1. **Scheme and port:** http/https only, on ports 80, 443, 8080 or 8443.
2. **Resolve the address:** the host name is resolved to IP addresses.
3. **Check every address:** any private, loopback, link-local, multicast or
   reserved address means the URL is refused (`ipaddress.is_global`).

To crawl a site running on your own machine, set
`ALLOW_PRIVATE_NETWORKS=true` in `backend/.env`. It is off by default.

**Known limitation:** the HTTP client resolves the name again when it
connects, so a hostile DNS server could answer differently the second time
(DNS rebinding). Fully closing that gap requires connecting to the
already-checked IP address.

The API is not a proxy either: it starts restricted jobs and never returns
page contents to the caller.

## Duplicate detection

1. **Same URL, different spelling:** URLs are normalized
   (`utils/urls.py`), so `/a`, `/a#top` and `HTTPS://Site/a` are one URL.
2. **A page declaring its preferred URL:** a same-site
   `<link rel="canonical">` decides which URL the document is stored under.
3. **Same content at different URLs:** SHA-256 of the whitespace-normalised
   text. If another document already has that hash, the URL is marked
   `duplicate` and not indexed again.
4. **Re-crawling:** a page already stored under the same canonical URL is
   updated and re-indexed, not duplicated.

## Testing without the internet

`Fetcher` accepts an HTTP client, DNS resolver, clock and sleep function. The
tests use a fake website (`httpx2.MockTransport`) with a robots.txt, a
redirect, a 404, an image, a noindex page and a duplicate page. They run in
under a second, with no real network and no waiting.

---

## Viva questions

**Q: What is the difference between crawling and indexing?**
Crawling *collects* pages: it follows links and downloads them. Indexing
*processes* what was collected, analysing text and building postings, so it
can be searched. PySearch also indexes pasted documents that were never
crawled.

**Q: How does the crawler avoid crawling the whole internet?**
It follows only the seed's own host (plus explicitly allowed domains), stops
at MAX_PAGES and MAX_DEPTH, runs one job at a time, and waits between
requests.

**Q: How do you prevent duplicate pages?**
URL normalization, rel=canonical, and a SHA-256 content hash checked before
indexing.

**Q: What is SSRF and how is it prevented?**
Tricking a server into requesting internal addresses. Every request and
redirect is checked: the host is resolved and refused if any address is not
public.

**Q: Why breadth-first?**
Pages closest to the seed are usually the most important, and BFS makes the
depth limit natural.
