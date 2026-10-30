import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Settings pointing at a throwaway database so tests never touch data/pysearch.db."""
    return Settings(DATABASE_PATH=tmp_path / "test.db", ENVIRONMENT="test")


@pytest.fixture
def client(settings) -> TestClient:
    # Using TestClient as a context manager runs the lifespan (init_db).
    with TestClient(create_app(settings)) as c:
        yield c


# ---------------------------------------------------------------- Phase 2

# Exactly what Phase 1's init_db() created. Used to prove the Phase 2
# migration upgrades a real Phase 1 database without losing anything.
PHASE1_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_info (
    id          INTEGER PRIMARY KEY CHECK (id = 1),  -- exactly one row
    version     INTEGER NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO schema_info (id, version, created_at) VALUES (1, 1, '2026-10-30 13:33:45');
"""


@pytest.fixture
def phase1_db(tmp_path):
    """A database file in the exact state Phase 1 left it (schema v1, WAL mode)."""
    import sqlite3

    path = tmp_path / "phase1.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(PHASE1_SCHEMA_SQL)
    conn.close()
    return path


@pytest.fixture
def db_path(tmp_path):
    """A fresh database migrated to the latest schema."""
    from app.database.database import init_db

    path = tmp_path / "pysearch.db"
    init_db(path)
    return path


@pytest.fixture
def documents(db_path):
    from app.services.document_service import DocumentService

    return DocumentService(db_path)


@pytest.fixture
def crawl(db_path):
    from app.services.crawl_service import CrawlService

    return CrawlService(db_path)


# ---------------------------------------------------------------- Phases 4-12

# A small corpus designed so expected rankings can be worked out by hand.
CORPUS = [
    ("https://learn.example.com/ml", "Machine Learning Tutorial",
     "Machine learning is a field of artificial intelligence. Learn machine learning with Python examples.", "Girish"),
    ("https://docs.example.org/python", "Python Programming",
     "Python is a powerful programming language used for web development and data science.", "Asha Rao"),
    ("https://blog.example.net/java", "Java vs Python",
     "Java and Python are popular. Java is statically typed while Python is dynamic. "
     "The University of London teaches both.", None),
    ("https://learn.example.com/db", "Database Indexing",
     "A database index such as a B-tree makes lookups fast. Inverted indexes power search engines.", None),
    ("https://sub.learn.example.com/net", "Networking Basics",
     "TCP provides reliable delivery while UDP is fast. London has many network engineers.", "Girish"),
]


@pytest.fixture
def search_index(db_path):
    from app.indexing.indexer import SearchIndex

    idx = SearchIndex()
    idx.load(db_path)
    return idx


@pytest.fixture
def index_service(db_path, search_index):
    from app.services.index_service import IndexService

    return IndexService(db_path, search_index)


@pytest.fixture
def corpus(index_service):
    """Load CORPUS; returns {url_suffix: doc_id} e.g. {'ml': 1, 'python': 2, ...}."""
    from app.services.document_service import DocumentCreate

    ids = {}
    for url, title, content, author in CORPUS:
        r = index_service.ingest(DocumentCreate(url=url, title=title, content=content, author=author))
        ids[url.rsplit("/", 1)[1]] = r.document.id
    return ids


@pytest.fixture
def engine(db_path, search_index, corpus):
    from app.search.search_engine import SearchEngine

    return SearchEngine(search_index, db_path)


# ---- fake website for crawler tests (no network, no real sleeping)

def make_site_handler(site: dict, calls: list | None = None, host: str = "site.example.com"):
    """site: path -> (status, content_type, body[, extra_headers])."""
    import httpx2 as httpx

    def handler(request):
        if calls is not None:
            calls.append(request.url.path)
        if request.url.host != host:
            return httpx.Response(404, text="unknown host")
        entry = site.get(request.url.path)
        if entry is None:
            return httpx.Response(404, headers={"content-type": "text/html"}, text="not found")
        status, ctype, body, *extra = entry
        headers = {"content-type": ctype, **(extra[0] if extra else {})}
        if callable(body):
            return body(request)
        return httpx.Response(status, headers=headers, content=body if isinstance(body, bytes) else body.encode())
    return handler


@pytest.fixture
def make_fetcher(settings):
    import httpx2 as httpx

    from app.crawler.fetcher import Fetcher

    def factory(handler, resolver=lambda host: ["93.184.216.34"], sleeps: list | None = None, s=None):
        return Fetcher(s or settings, client=httpx.Client(transport=httpx.MockTransport(handler)),
                       resolver=resolver, sleep=(sleeps.append if sleeps is not None else (lambda _: None)))
    return factory
