"""All HTTP endpoints, through FastAPI's TestClient."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import CORPUS, make_site_handler


def add_corpus(client):
    for url, title, content, author in CORPUS:
        assert client.post("/documents", json={"url": url, "title": title, "content": content, "author": author}).status_code == 201


# ---------------------------------------------------------------- documents
def test_add_get_list_delete_document(client):
    r = client.post("/documents", json={"url": "https://x.example.com/a", "title": "Zebra", "content": "Zebras have stripes."})
    assert r.status_code == 201
    doc = r.json()["document"]
    assert r.json()["created"] is True and doc["indexed"] is True and doc["canonical_url"] == "https://x.example.com/a"

    got = client.get(f"/documents/{doc['id']}").json()
    assert got["content"] == "Zebras have stripes." and got["top_terms"][0]["term"] in {"zebra", "stripe"}

    listing = client.get("/documents").json()
    assert listing["total"] == 1 and "content" not in listing["documents"][0] and listing["documents"][0]["preview"]

    assert client.get("/search", params={"q": "zebra"}).json()["total"] == 1
    assert client.delete(f"/documents/{doc['id']}").json() == {"deleted": doc["id"]}
    assert client.get(f"/documents/{doc['id']}").status_code == 404
    assert client.get("/search", params={"q": "zebra"}).json()["total"] == 0


def test_add_same_url_updates(client):
    client.post("/documents", json={"url": "https://x.example.com/a", "content": "first version"})
    r = client.post("/documents", json={"url": "HTTPS://X.example.com/a#frag", "content": "second version"})
    assert r.status_code == 200 and r.json()["created"] is False
    assert client.get("/documents").json()["total"] == 1
    assert client.get("/search", params={"q": "first"}).json()["total"] == 0
    assert client.get("/search", params={"q": "second"}).json()["total"] == 1


def test_add_html_document_extracts_text_and_metadata(client):
    html = ("<html lang='en'><head><title>Page Title</title><meta name='author' content='Meera'>"
            "<script>bad()</script></head><body><nav>menu</nav><main><p>Real content here.</p></main></body></html>")
    doc = client.post("/documents", json={"url": "https://x.example.com/p", "content": html, "format": "html"}).json()["document"]
    assert doc["title"] == "Page Title" and doc["author"] == "Meera" and doc["language"] == "en"
    assert doc["content"] == "Real content here."


@pytest.mark.parametrize(
    "body, code",
    [
        ({"url": "https://x.example.com/", "content": ""}, 422),
        ({"url": "https://x.example.com/", "content": "   "}, 422),
        ({"url": "ftp://x.example.com/", "content": "hi"}, 422),
        ({"url": "not a url", "content": "hi"}, 422),
        ({"content": "hi"}, 422),
        ({"url": "https://x.example.com/", "content": "hi", "format": "pdf"}, 422),
    ],
)
def test_add_document_validation(client, body, code):
    r = client.post("/documents", json=body)
    assert r.status_code == code and "detail" in r.json()


def test_delete_missing_document_404(client):
    r = client.delete("/documents/999")
    assert r.status_code == 404 and r.json() == {"detail": "Document 999 not found"}


def test_list_documents_pagination_and_domain_filter(client):
    add_corpus(client)
    page = client.get("/documents", params={"limit": 2, "offset": 1}).json()
    assert page["total"] == 5 and len(page["documents"]) == 2
    assert client.get("/documents", params={"domain": "learn.example.com"}).json()["total"] == 2


def test_load_sample(client):
    r = client.post("/documents/sample").json()
    assert r["created"] == 44 and r["documents"] == 44
    assert client.post("/documents/sample").json()["updated"] == 44   # idempotent


# ---------------------------------------------------------------- search
def test_search_endpoint(client):
    add_corpus(client)
    r = client.get("/search", params={"q": "machine learning", "explain": True}).json()
    assert r["total"] >= 1 and r["results"][0]["title"] == "Machine Learning Tutorial"
    assert r["results"][0]["explain"] and r["history_id"] is not None
    assert r["mode"] == "basic" and r["ranker"] == "bm25"
    assert client.get("/search", params={"q": "python", "ranker": "tfidf"}).json()["ranker"] == "tfidf"


@pytest.mark.parametrize(
    "params",
    [{"q": ""}, {}, {"q": "x", "mode": "magic"}, {"q": "x", "ranker": "pagerank"},
     {"q": "x", "page": 0}, {"q": "x", "page_size": 500}, {"q": "x" * 501}],
)
def test_search_validation(client, params):
    assert client.get("/search", params=params).status_code == 422


def test_search_history_click_and_clear(client):
    add_corpus(client)
    r = client.get("/search", params={"q": "python"}).json()
    client.get("/search", params={"q": "python"})
    client.get("/search", params={"q": "python", "page": 2})            # later pages are not recorded
    client.get("/search", params={"q": "zzz", "record": False})          # opt-out
    hist = client.get("/search/history").json()
    assert hist["recent"][0]["query"] == "python" and hist["recent"][0]["times"] == 2
    assert hist["popular"][0] == {"query": "python", "count": 2, "avg_latency_ms": hist["popular"][0]["avg_latency_ms"], "clicks": 0}

    doc_id = r["results"][0]["id"]
    assert client.post("/search/click", json={"history_id": r["history_id"], "doc_id": doc_id}).json() == {"ok": True}
    assert client.get("/search/history").json()["popular"][0]["clicks"] == 1
    assert client.post("/search/click", json={"history_id": 9999, "doc_id": doc_id}).status_code == 404
    assert client.post("/search/click", json={"history_id": r["history_id"], "doc_id": 9999}).status_code == 404

    assert client.delete("/search/history").json()["deleted"] == 2
    assert client.get("/search/history").json()["recent"] == []


def test_suggest_endpoint(client):
    add_corpus(client)
    client.get("/search", params={"q": "machine learning tutorial"})
    s = client.get("/search/suggest", params={"q": "mach"}).json()
    assert s["suggestions"][0] == "machine learning tutorial" and "machine" in s["suggestions"]
    assert client.get("/search/suggest", params={"q": ""}).status_code == 422


# ---------------------------------------------------------------- index + stats
def test_index_endpoints(client):
    add_corpus(client)
    assert client.post("/index/build").json()["indexed"] == 0
    rebuilt = client.post("/index/rebuild").json()
    assert rebuilt["indexed"] == 5 and rebuilt["stats"]["documents"] == 5
    stats = client.get("/index/stats").json()
    assert stats["terms"] > 20 and stats["top_terms"][0]["df"] >= stats["top_terms"][-1]["df"]
    assert {"term", "word", "df", "cf"} <= set(stats["top_terms"][0])


def test_stats_endpoint(client):
    add_corpus(client)
    client.get("/search", params={"q": "python"})
    s = client.get("/stats").json()
    assert s["documents"] == 5 and s["indexed_documents"] == 5 and s["domains"] == 4  # learn.example.com twice
    assert s["search"]["searches"] == 1 and s["top_queries"][0]["query"] == "python"
    assert s["index_size_bytes"] > 0 and s["database_size_bytes"] > 0
    assert set(s["crawl"]) >= {"jobs", "pages_fetched", "pages_failed", "duplicates", "blocked_by_robots"}


def test_index_survives_restart(settings):
    with TestClient(create_app(settings)) as c:
        add_corpus(c)
    with TestClient(create_app(settings)) as c:       # new process: index loaded from SQLite
        assert c.get("/index/stats").json()["documents"] == 5
        assert c.get("/search", params={"q": '"machine learning"'}).json()["total"] == 1


# ---------------------------------------------------------------- crawl
@pytest.fixture
def crawl_client(settings, make_fetcher):
    site = {"/robots.txt": (404, "text/plain", ""),
            "/": (200, "text/html", '<title>Home</title><p>Zebra docs</p><a href="/a">a</a>'),
            "/a": (200, "text/html", "<title>A</title><p>Zebra stripes</p>")}
    app = create_app(settings, fetcher_factory=lambda: make_fetcher(make_site_handler(site)),
                     resolver=lambda host: ["93.184.216.34"])
    with TestClient(app) as c:
        yield c


def test_crawl_api_flow(crawl_client):
    r = crawl_client.post("/crawl", json={"url": "https://site.example.com/", "max_depth": 1, "max_pages": 10})
    assert r.status_code == 202
    job_id = r.json()["job"]["id"]
    crawl_client.app.state.crawl_manager.wait(5)
    status = crawl_client.get("/crawl/status", params={"job_id": job_id}).json()
    assert status["job"]["status"] == "completed" and status["job"]["pages_fetched"] == 2 and status["busy"] is False
    assert crawl_client.get("/crawl/status").json()["job"]["id"] == job_id      # latest job
    assert crawl_client.get("/crawl/jobs").json()["jobs"][0]["id"] == job_id
    assert crawl_client.get("/search", params={"q": "zebra"}).json()["total"] == 2


def test_crawl_limits_capped_by_settings(crawl_client, settings):
    r = crawl_client.post("/crawl", json={"url": "https://site.example.com/", "max_depth": 5, "max_pages": 5000}).json()
    assert r["limits"]["max_depth"] == settings.MAX_DEPTH and r["limits"]["max_pages"] == settings.MAX_PAGES
    crawl_client.app.state.crawl_manager.wait(5)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "not-a-url", "",
                                 "http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:8000/",
                                 "http://192.168.1.1/", "http://localhost/", "https://example.com:22/"])
def test_crawl_rejects_bad_urls(crawl_client, url):
    r = crawl_client.post("/crawl", json={"url": url})
    assert r.status_code == 422 and r.json()["detail"]
    assert crawl_client.get("/crawl/jobs").json()["jobs"] == []   # no job was created


def test_crawl_status_unknown_job(crawl_client):
    assert crawl_client.get("/crawl/status", params={"job_id": 999}).status_code == 404


def test_cancel_finished_job_conflict(crawl_client):
    job_id = crawl_client.post("/crawl", json={"url": "https://site.example.com/", "max_depth": 0}).json()["job"]["id"]
    crawl_client.app.state.crawl_manager.wait(5)
    assert crawl_client.post(f"/crawl/{job_id}/cancel").status_code == 409
    assert crawl_client.post("/crawl/999/cancel").status_code == 404


def test_crawl_status_empty(client):
    assert client.get("/crawl/status").json() == {"job": None, "busy": False}


# ---------------------------------------------------------------- general
def test_openapi_lists_all_endpoints(client):
    paths = set(client.get("/openapi.json").json()["paths"])
    assert {"/", "/health", "/analyze", "/search", "/search/suggest", "/search/history", "/search/click",
            "/documents", "/documents/{doc_id}", "/documents/sample", "/index/build", "/index/rebuild",
            "/index/stats", "/stats", "/crawl", "/crawl/status", "/crawl/jobs", "/crawl/{job_id}/cancel"} <= paths


def test_cors_allows_delete_from_frontend(client):
    r = client.options("/search/history", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "DELETE"})
    assert r.status_code == 200 and "DELETE" in r.headers["access-control-allow-methods"]
