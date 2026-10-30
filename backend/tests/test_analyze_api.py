"""Phase 3: POST /analyze."""

from app.api.analyze import MAX_INPUT_CHARS


def test_analyze_plain_text(client):
    res = client.post("/analyze", json={"text": "Python is a powerful programming language."})
    assert res.status_code == 200
    body = res.json()
    assert body["terms"] == ["python", "power", "program", "languag"]
    assert body["extracted"] is None
    assert body["stats"] == {"tokens": 6, "terms": 4, "unique_terms": 4, "stopwords_removed": 2}
    is_token = body["tokens"][1]
    assert is_token == {"original": "is", "normalized": "is", "term": None,
                        "position": 1, "start": 7, "end": 9, "dropped": "stopword"}


def test_analyze_html(client):
    html = "<html lang='en'><head><title>T</title><script>x()</script></head><body><nav>menu</nav><p>Search engines rank pages</p></body></html>"
    body = client.post("/analyze", json={"text": html, "format": "html"}).json()
    assert body["extracted"]["title"] == "T"
    assert body["extracted"]["language"] == "en"
    assert body["cleaned_text"] == "Search engines rank pages"
    assert body["terms"] == ["search", "engin", "rank", "page"]


def test_analyze_options(client):
    body = client.post("/analyze", json={"text": "The running dogs", "remove_stopwords": False, "stem": False}).json()
    assert body["terms"] == ["the", "running", "dogs"]
    assert body["analyzer"]["stem"] is False


def test_analyze_validation(client):
    assert client.post("/analyze", json={"text": ""}).status_code == 422
    assert client.post("/analyze", json={"text": "   "}).status_code == 422
    assert client.post("/analyze", json={"text": "x", "format": "pdf"}).status_code == 422
    assert client.post("/analyze", json={}).status_code == 422
    assert client.post("/analyze", json={"text": "a" * (MAX_INPUT_CHARS + 1)}).status_code == 422


def test_analyze_does_not_touch_database(client, settings):
    import sqlite3

    client.post("/analyze", json={"text": "nothing is stored"})
    conn = sqlite3.connect(settings.DATABASE_PATH)
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    conn.close()
