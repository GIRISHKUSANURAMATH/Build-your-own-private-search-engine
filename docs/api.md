# API reference

Base URL: `http://127.0.0.1:8000`. Interactive documentation, where you can
try every endpoint, is at **http://127.0.0.1:8000/docs**.

Errors are always JSON in the form `{"detail": "..."}`, with these status
codes:

| Code | When |
|------|------|
| 404 | Document, crawl job or search not found |
| 409 | Conflict: a crawl is already running, or the job is already finished |
| 415 | Unsupported content type |
| 422 | Invalid input (validation, bad URL, blocked URL) |

## System

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Name, version, links |
| GET | `/health` | Status, uptime, database connectivity, schema version |

## Search

### `GET /search`

| Parameter | Default | Description |
|-----------|---------|-------------|
| `q` | required | Query, 1–500 characters. Syntax: [search.md](search.md#2-query-syntax) |
| `mode` | `basic` | `basic` (any words), `boolean` (all words), `phrase`, `fuzzy` |
| `ranker` | `bm25` | `bm25` or `tfidf` |
| `page`, `page_size` | 1, 10 | `page_size` ≤ 50 |
| `explain` | false | Include per-term score breakdowns |
| `record` | true | Save the query in search history |

```json
{
  "query": "machine learning", "mode": "basic", "ranker": "bm25",
  "parsed": "OR(machine, learning)", "total": 9, "page": 1, "page_size": 10, "pages": 1,
  "took_ms": 7.3, "did_you_mean": null, "expansions": {}, "warnings": [], "history_id": 12,
  "results": [{
    "id": 27, "rank": 1, "title": "Introduction to Machine Learning",
    "title_segments": [{"text": "Introduction to ", "highlight": false}, {"text": "Machine", "highlight": true}],
    "url": "https://learn.example.com/machine-learning-introduction", "domain": "learn.example.com",
    "author": "Girish", "score": 19.45, "matched_terms": ["learning", "machine"],
    "snippet": {"text": "… Machine learning powers spam filters …", "segments": [...]},
    "created_at": "...", "indexed_at": "..."
  }]
}
```

### Other search endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/search/suggest?q=mach` | `{"suggestions": ["machine learning tutorial", "machine"]}` |
| GET | `/search/history?limit=10` | `{"recent": [...], "popular": [...]}` |
| DELETE | `/search/history` | Clear history: `{"deleted": n}` |
| POST | `/search/click` | `{"history_id": 12, "doc_id": 27}`: record which result was opened |

## Documents

| Method | Path | Description |
|--------|------|-------------|
| POST | `/documents` | Add (or update, if the canonical URL exists) **and index** a document |
| GET | `/documents?limit=20&offset=0&domain=` | List (newest first) with previews |
| GET | `/documents/{id}` | Full document, top terms, term count |
| DELETE | `/documents/{id}` | Remove from the collection and the index |
| POST | `/documents/sample` | Load the 44-document demo collection (safe to repeat) |

`POST /documents` body:
```json
{"url": "https://example.com/notes", "title": "optional", "content": "text or HTML",
 "format": "text | html", "author": "optional", "language": "optional"}
```
With `"format": "html"`, the title, author and language are extracted from the
page when not given. The response is **201** if the document was created and
**200** if it was updated.

## Index and statistics

| Method | Path | Description |
|--------|------|-------------|
| POST | `/index/build` | Index new or changed documents |
| POST | `/index/rebuild` | Rebuild the whole index from the documents table |
| GET | `/index/stats` | Documents, terms, postings, average length, last indexed, top terms |
| GET | `/stats` | Everything for the dashboard: collection, crawl, index, search analytics |

## Crawler

| Method | Path | Description |
|--------|------|-------------|
| POST | `/crawl` | `{"url": "...", "max_depth": 1, "max_pages": 20, "allowed_domains": []}` → **202** with the job and effective limits |
| GET | `/crawl/status?job_id=` | One job (latest if no id) plus `busy` |
| GET | `/crawl/jobs` | Recent jobs |
| POST | `/crawl/{id}/cancel` | Cancel a pending or running job |

## Text processing

| Method | Path | Description |
|--------|------|-------------|
| POST | `/analyze` | `{"text": "...", "format": "text|html", "remove_stopwords": true, "stem": true}`: every pipeline stage, nothing stored |
