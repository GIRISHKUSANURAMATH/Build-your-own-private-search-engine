# PySearch — Architecture

## The big picture

```
                ┌──────────────────── WRITE PATH (slow, offline) ────────────────────┐
 Start URLs ──► Crawler ──► Parser ──► Text pipeline ──► Indexer ──► SQLite (source of truth)
               (robots,     (HTML →    (clean, tokenize,  (inverted +       │
                limits)      text)      stop words, stem)  forward index)   │ load at startup
                                                                            ▼
                ┌──────────────────── READ PATH (fast, per query) ───────────┐
 User query ──► Query parser ──► Candidate retrieval ──► Ranker ──► Snippets ──► JSON ──► React UI
               (AST: AND/OR/     (postings lookups,     (TF-IDF     (highlight)
                NOT/"phrase"/     set ops, positions)    or BM25)
                field:/fuzzy~)
```

The single most important idea: **crawling/indexing and searching are separate
paths.** Indexing is allowed to be slow (it happens once per document). Search
must be fast (it happens on every keystroke for autocomplete). Everything about
the index layout exists to make the read path cheap.

## Layers (backend/app)

| Package     | Responsibility                                         | Knows about HTTP? |
|-------------|--------------------------------------------------------|-------------------|
| `api/`      | FastAPI routes, request/response models, validation    | yes               |
| `services/` | Orchestrates a use case (e.g. "crawl then index")      | no                |
| `crawler/`  | Fetch pages safely: queue, robots.txt, limits, SSRF    | no                |
| `indexing/` | Text pipeline + inverted/forward index construction    | no                |
| `ranking/`  | TF-IDF, BM25 — pure functions over index statistics    | no                |
| `search/`   | Query parser, retrieval, fuzzy matching, snippets      | no                |
| `database/` | SQLite connection, schema migrations, repositories     | no                |

Rule: only `api/` imports FastAPI. Everything else is plain Python, which is
why it can be unit-tested without starting a server.

## Key design decisions

1. **Raw `sqlite3`, no ORM.** The postings table will hold hundreds of
   thousands of rows; we want explicit bulk inserts and to see every SQL query.
2. **Index lives in memory for search, SQLite for persistence.** On startup the
   inverted index is loaded into a Python `dict[str, list[Posting]]`. A query
   then costs a few dictionary lookups instead of SQL joins. For a personal
   collection (a few thousand pages) this fits comfortably in RAM. SQLite stays
   the source of truth so a restart doesn't require re-crawling.
3. **Positions are stored in postings.** Needed for phrase search and for
   snippet selection. Costs disk space; worth it.
4. **Two token streams.** Stemmed tokens go into the index (so "running"
   matches "run"); the original text is kept for snippets and for fuzzy
   matching against real words.
5. **Ranking is pluggable.** `Ranker` is an interface; TF-IDF and BM25 are two
   implementations selected per request (`/search?q=...&ranker=bm25`).
6. **Crawler is deliberately restricted.** Same-domain by default, hard page
   and depth caps, robots.txt honoured, private/loopback IPs refused (SSRF).
   The API never fetches arbitrary URLs on behalf of a caller.

## Phase 1 scope (historical)

Phase 1 delivered only the skeleton: config, SQLite connection with a `schema_info`
version table, `/` and `/health`, and a React homepage that shows whether the
API is reachable. Later phases filled each package; see "Phases 4-12" below.

## Phase 2 data model

### Entity relationships

```
┌──────────────┐ 1        * ┌──────────────┐
│  crawl_jobs  │────────────│  documents   │   documents.crawl_job_id → crawl_jobs.id
│              │            │              │   (ON DELETE SET NULL)
└──────┬───────┘            └──────▲───────┘
       │ 1                         │ 0..1
       │                           │
       │ *                         │
┌──────▼───────────────────────────┴───────┐
│                  urls                    │   urls.crawl_job_id → crawl_jobs.id
│  one row per normalized URL ever seen    │   urls.document_id  → documents.id
└──────────────────────────────────────────┘   (both ON DELETE SET NULL)
```

* **`crawl_jobs`** — one row per crawl run: its seed, limits, life-cycle
  status and counters.
* **`documents`** — one row per stored page. Identity = `canonical_url`.
* **`urls`** — the persistent crawl state of every URL: discovered, fetched,
  failed, blocked... A URL can exist without a document (it failed, was
  skipped, or hasn't been fetched yet), which is why URLs and documents are
  separate tables.

`ON DELETE SET NULL` everywhere: deleting a job or a document removes that
row only. URL history and other documents survive, with the link cleared.

### Why `urls` is separate from `documents`

| Question                                         | Answered by |
|--------------------------------------------------|-------------|
| "Have I already seen this link?"                 | `urls` (UNIQUE `normalized_url`) |
| "Which pages failed, and why? When do I retry?"  | `urls.status`, `last_error`, `attempts`, `next_crawl_at` |
| "What text do I index and show in results?"      | `documents` |

Putting failures and pending links into `documents` would fill the table
we index with rows that have no content.

### Columns and constraints

**documents**

| Column          | Type    | Constraint / note |
|-----------------|---------|-------------------|
| id              | INTEGER | PRIMARY KEY |
| url             | TEXT    | NOT NULL — exactly as given/fetched |
| canonical_url   | TEXT    | NOT NULL **UNIQUE** — output of `normalize_url()` |
| title, content  | TEXT    | NOT NULL, default `''` |
| author          | TEXT    | nullable (most web pages don't expose one) |
| domain          | TEXT    | NOT NULL — host of canonical_url |
| content_type    | TEXT    | NOT NULL, default `text/html` |
| language        | TEXT    | nullable |
| http_status     | INTEGER | NULL or 100–599 |
| content_hash    | TEXT    | NOT NULL, exactly 64 chars (SHA-256 hex) |
| crawl_job_id    | INTEGER | FK → crawl_jobs, ON DELETE SET NULL |
| created_at      | TEXT    | UTC ISO-8601 with ms, set on insert |
| updated_at      | TEXT    | maintained by trigger `trg_documents_updated_at` |

**crawl_jobs**

| Column           | Constraint / note |
|------------------|-------------------|
| seed_url         | NOT NULL, stored normalized |
| status           | `pending`, `running`, `completed`, `failed`, `cancelled` |
| max_depth        | NULL or ≥ 0 |
| max_pages        | NULL or ≥ 1 |
| pages_discovered / pages_fetched / pages_failed | NOT NULL, ≥ 0 |
| error            | nullable text |
| created_at / started_at / completed_at | `completed_at` requires `started_at` (table CHECK) |

Allowed status changes (enforced in `CrawlService`):
`pending → running | cancelled`, `running → completed | failed | cancelled`.
Finished states are final.

**urls**

| Column           | Constraint / note |
|------------------|-------------------|
| url              | NOT NULL — as discovered |
| normalized_url   | NOT NULL **UNIQUE** |
| domain           | NOT NULL |
| status           | `pending`, `fetched`, `failed`, `skipped`, `duplicate`, `blocked` |
| http_status      | NULL or 100–599 |
| depth            | NULL or ≥ 0 |
| attempts         | ≥ 0; +1 every time a request is made |
| last_error       | nullable |
| content_hash     | NULL or 64 chars |
| document_id      | FK → documents, ON DELETE SET NULL |
| crawl_job_id     | FK → crawl_jobs (job that last touched the URL), ON DELETE SET NULL |
| discovered_at / last_fetched_at / next_crawl_at | UTC ISO-8601 text |

### Indexes and why each exists

An index is a sorted lookup structure (a B-tree). Without one, `WHERE x = ?`
reads every row; with one, SQLite jumps straight to the matches. Each index
costs disk space and makes writes a little slower, so every one here backs a
specific query. `tests/test_migrations.py` uses `EXPLAIN QUERY PLAN` to prove
SQLite really uses them.

| Index | Query it serves |
|-------|-----------------|
| `documents.canonical_url` (automatic, UNIQUE) | "Is this page already stored?" and the duplicate guarantee itself |
| `urls.normalized_url` (automatic, UNIQUE) | "Have I seen this link?", run for every link on every crawled page |
| `idx_documents_content_hash` | Exact-duplicate detection: "does another document have identical text?" Not UNIQUE on purpose: what to do with duplicates is decided in code (Phase 9) |
| `idx_documents_domain` | `site:` filter (Phase 8) and per-domain stats (Phase 11) |
| `idx_urls_status_next_crawl` (composite) | Scheduler: `WHERE status='pending' ORDER BY next_crawl_at`. Equality column first, sort column second, so SQLite can filter and return rows already sorted |
| `idx_documents_crawl_job`, `idx_urls_crawl_job`, `idx_urls_document` | Foreign-key columns. SQLite does **not** index these automatically. Without them, deleting a job/document scans the whole child table to apply `SET NULL`, and "pages of job N" is a full scan |
| `idx_crawl_jobs_status` | Dashboard: "which job is running?" |

### Migrations

`migrations.py` maps version → list of SQL statements. `init_db()` (called
at startup):

1. reads `schema_info.version` (0 for an empty file);
2. if the file is an existing database with pending migrations, copies it to
   `<name>.v<N>.bak` with SQLite's online backup API (safe under WAL);
3. for each pending version: `BEGIN IMMEDIATE` → run statements → bump the
   version → `COMMIT`. On any error: `ROLLBACK` and raise `MigrationError`.

`BEGIN IMMEDIATE` takes the write lock up front and the version is re-checked
under that lock, so two processes starting at once can't apply the same
migration twice.

### Layering in Phase 2

```
services/  (DocumentService, CrawlService)   ← normalization, hashing, life-cycle rules,
     │                                          transaction boundaries
     ▼
database/repositories.py                     ← the only SQL; takes a connection, never commits
     │
     ▼
database/database.py  get_connection()       ← commit on success, rollback on error
```

One service method = one `get_connection()` block = one transaction. For
example, `CrawlService.record_fetch_success()` upserts the document, updates
the URL state and bumps the job counter on a single connection, so either all
three happen or none do (tested by forcing the second step to fail).

## Phase 3 text pipeline

Full concepts and viva Q&A: [text-processing.md](text-processing.md).

### Where it sits

```
                       ┌────────────── indexing/ ──────────────────────────────────┐
 HTML / plain text ──► │ html_extractor ─► cleaner ─► Analyzer                     │ ──► Token stream
 (crawler, Phase 9)    │                     │        (tokenizer ─► normalizer ─►  │     (term, normalized,
                       │                     │         stopwords ─► stemmer)       │      original, position,
                       └─────────────────────┼─────────────────────────────────────┘      start, end)
                                             ▼
                                 documents.content (Phase 2 table)
```

### Contracts later phases rely on

1. **`documents.content` stores the *cleaned* text** (the output of
   `clean_text` / `extract_html().text`). Token offsets (`start`, `end`) are
   offsets into exactly that string, so snippets (Phase 5) can highlight
   without re-tokenizing.
2. **One Analyzer for documents and queries.** `DEFAULT_ANALYZER` is that
   shared instance. If its configuration ever changes (say, stemming turned
   off), the index must be rebuilt, because old terms won't match new
   queries.
3. **Positions count every token**, including dropped stop words. Phase 4
   stores these positions in postings; Phase 8 phrase search compares
   position differences.
4. **`Token.normalized` is a real word, `Token.term` is a stem.** The inverted
   index is keyed by `term`; the fuzzy-matching vocabulary (Phase 8) uses
   `normalized`.

### Design decisions

| Decision | Why |
|----------|-----|
| BeautifulSoup with `html.parser`, no lxml | Pure Python (no compiled wheels); fast enough at our scale; spec allows a library for HTML parsing |
| Hand-written Porter stemmer, no NLTK | NLTK would add 6 packages for one algorithm, and stemming is core IR logic we want to understand. Checked against NLTK on 60,013 words with zero differences |
| Character-scanning tokenizer instead of `\w+` regex | `\w` excludes combining marks, which breaks Kannada/Hindi/Tamil words |
| Accent folding on Latin letters only | The combining marks of Indic scripts are vowel signs, not accents |
| Stop-word removal configurable | Lets Phase 6 show IDF doing the same job, and supports stop-word phrase queries if needed |
| `/analyze` endpoint | Lets you demo every pipeline stage live; read-only, input capped at 200,000 characters |
| Stemming results cached (`lru_cache`, 100k words) | Real text repeats words constantly; caching makes the analyzer several times faster |

### Not in Phase 3 (on purpose)

* No index yet. Phase 4 consumes the Token stream.
* No link extraction. Phase 9's crawler needs links; the extractor only
  reads `<link rel="canonical">`.
* `DocumentService` is unchanged. Phase 4/9 will call the extractor before
  storing a document.

## Phases 4-12: the complete engine

### Request flows

**Adding a document** (`POST /documents`, the crawler, `manage.py load-sample`):
```
IndexService.ingest ─► DocumentService (normalize URL, SHA-256, insert/update row)
                    └► SearchIndex.index_document ─► Analyzer (title + body)
                         ├─ one SQLite transaction: terms, postings, doc_stats, vocabulary, index_meta
                         └─ after commit: in-memory InvertedIndex x2, ForwardIndex, vocabulary, doc_meta
```

**Searching** (`GET /search`):
```
parse_query ─► AST ─► SearchEngine._eval (set operations on postings, phrase positions,
                                          site:/author: filters, fuzzy expansion)
            ─► Ranker (BM25 | TF-IDF, title boost, explain) ─► page ─► load rows ─► snippets
            ─► HistoryService.record (first page only)
```

**Crawling** (`POST /crawl` → background thread):
```
CrawlManager ─► Crawler: CrawlQueue (BFS) ─► RobotsCache ─► Fetcher (SSRF check, rate limit,
                retries, redirects, size cap) ─► extract_links + IndexService.ingest_raw
             ─► CrawlService (url states, job counters)
```

### Packages

| Package | Phase | Main pieces |
|---------|-------|-------------|
| `indexing/` | 3–4 | analyzer pipeline; `InvertedIndex`, `ForwardIndex`, `SearchIndex` (indexer) |
| `ranking/` | 6–7 | `tfidf.py`, `bm25.py` (formulas), `ranker.py` (fields, boost, explain) |
| `search/` | 5, 8 | `query_parser.py`, `search_engine.py`, `fuzzy.py`, `snippet.py`, `suggest.py` |
| `crawler/` | 9 | `security.py` (SSRF), `robots.py`, `fetcher.py` (+ `RateLimiter`), `parser.py`, `url_queue.py`, `crawler.py`, `jobs.py` |
| `services/` | 2–11 | `index_service.py` (store + index), `history_service.py`, `statistics.py`, `sample_data.py` |
| `evaluation/` | 12 | `metrics.py` (P/R/F1/AP/RR/nDCG), `evaluate.py` (TF-IDF vs BM25, benchmark) |
| `api/` | all | `search.py`, `documents.py`, `index.py`, `crawler.py`, `analyze.py`, `health.py`, `deps.py` |

**Frontend → backend:** the React app calls `/api/...` on its own origin, and
Vite's proxy forwards to FastAPI on port 8000. Same-origin requests need no
CORS and aren't blocked by browser features that restrict cross-port
`localhost` calls. CORS is still configured for direct use (`VITE_API_URL`).

Objects shared by requests (the index, search engine, suggester, services and
crawl manager) are created once in `main.py`'s lifespan and stored on
`app.state`. Route handlers get them through `api/deps.py`.

### Schema v3 additions

| Table | Key | Index | Why |
|-------|-----|-------|-----|
| `terms` | id; UNIQUE(field, term) | automatic (unique) | term → id lookups while indexing |
| `postings` | PK(term_id, doc_id), WITHOUT ROWID | `idx_postings_doc(doc_id)` | PK = term → documents; the extra index = document → postings, for re-indexing and deleting |
| `doc_stats` | doc_id → documents ON DELETE CASCADE | PK | BM25 lengths; content hash to detect changes |
| `vocabulary` | word | PK | fuzzy matching and autocomplete over real words |
| `search_history` | id | `created_at`, `query` | recent searches; popular and prefix queries |
| `index_meta` | key | PK | last indexing time, analyzer signature (triggers a rebuild if it changes) |

Deleting a document cascades to its postings and doc_stats. The API deletes
through `IndexService.delete`, so the in-memory index and `terms.df` are
updated too.

### Error handling

Domain exceptions are mapped to HTTP responses in `main.py`, always as
`{"detail": ...}`:

| Exception | Status |
|-----------|--------|
| `NotFoundError` | 404 |
| `DuplicateDocumentError`, `InvalidStatusTransition`, `CrawlBusyError` | 409 |
| `InvalidURLError`, `BlockedURLError`, validation errors | 422 |
| `UnsupportedContentType` | 415 |

### Security summary

| Threat | Defence |
|--------|---------|
| SQL injection | parameter binding everywhere; column names only from whitelists |
| XSS from crawled pages | snippets and titles are returned as text segments; React renders text; no `innerHTML` |
| SSRF through the crawler | resolve the host and refuse non-public IPs before every request and redirect; restricted ports |
| Abusing the crawler as a proxy | page bodies are never returned; one job at a time; limits capped by server settings |
| Resource exhaustion | input size limits (query 500 chars, documents 2 MB, HTML 5 MB), timeouts, page and depth caps |
| Cross-origin calls | CORS limited to the configured frontend origins |
| Secrets in git | `.env` is git-ignored; `.env.example` contains no secrets |
