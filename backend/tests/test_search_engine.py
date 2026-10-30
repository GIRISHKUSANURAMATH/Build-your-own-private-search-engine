"""Phases 5-8: end-to-end search on a small corpus."""

import pytest

from app.services.document_service import DocumentCreate


def ids(res):
    return [r["id"] for r in res["results"]]


def test_basic_search_ranks_documents_with_more_terms_first(engine, corpus):
    res = engine.search("python programming")
    assert res["parsed"] == "OR(python, programming)"
    assert ids(res)[0] == corpus["python"]          # has both words (and both in title)
    assert set(ids(res)) == {corpus["python"], corpus["java"], corpus["ml"]}
    assert res["total"] == 3 and res["took_ms"] >= 0


def test_boolean_and(engine, corpus):
    assert ids(engine.search("python AND java")) == [corpus["java"]]
    assert ids(engine.search("python java", mode="boolean")) == [corpus["java"]]


def test_boolean_or(engine, corpus):
    assert set(ids(engine.search("tcp OR lookups"))) == {corpus["net"], corpus["db"]}


def test_boolean_not(engine, corpus):
    assert set(ids(engine.search("python NOT java"))) == {corpus["python"], corpus["ml"]}
    assert set(ids(engine.search("python -java"))) == {corpus["python"], corpus["ml"]}


def test_not_alone_returns_everything_else(engine, corpus):
    assert set(ids(engine.search("NOT python"))) == {corpus["db"], corpus["net"]}


def test_grouping(engine, corpus):
    assert set(ids(engine.search("(java OR tcp) AND london"))) == {corpus["java"], corpus["net"]}


def test_phrase_requires_adjacent_words(engine, corpus):
    assert ids(engine.search('"machine learning"')) == [corpus["ml"]]
    assert ids(engine.search('"python machine"')) == []
    assert ids(engine.search('"statically typed"')) == [corpus["java"]]


def test_phrase_keeps_stopword_gap(engine, corpus):
    assert ids(engine.search('"university of london"')) == [corpus["java"]]
    assert ids(engine.search('"university london"')) == []   # no gap in the query -> no match


def test_phrase_mode(engine, corpus):
    assert ids(engine.search("search engines", mode="phrase")) == [corpus["db"]]


def test_stopword_only_phrase_warns(engine):
    res = engine.search('"the of"')
    assert res["total"] == 0 and res["warnings"]


def test_title_field(engine, corpus):
    res = engine.search("title:python")
    assert set(ids(res)) == {corpus["python"], corpus["java"]}
    assert ids(engine.search('title:"machine learning"')) == [corpus["ml"]]


def test_site_filter_includes_subdomains(engine, corpus):
    assert set(ids(engine.search("site:learn.example.com"))) == {corpus["ml"], corpus["db"], corpus["net"]}
    assert set(ids(engine.search("site:https://docs.example.org/anything"))) == {corpus["python"]}
    assert ids(engine.search("python site:blog.example.net")) == [corpus["java"]]


def test_author_filter(engine, corpus):
    assert set(ids(engine.search("author:girish"))) == {corpus["ml"], corpus["net"]}
    assert ids(engine.search("london author:girish")) == [corpus["net"]]
    assert ids(engine.search('author:"asha rao"')) == [corpus["python"]]  # every word of the name must match
    assert ids(engine.search('author:"asha smith"')) == []
    assert ids(engine.search("author:asha")) == [corpus["python"]]


def test_fuzzy_operator_and_mode(engine, corpus):
    res = engine.search("machne~")
    assert ids(res) == [corpus["ml"]] and res["expansions"] == {"machne": ["machine"]}
    res = engine.search("pythn programing", mode="fuzzy")
    assert corpus["python"] == ids(res)[0]


def test_did_you_mean(engine):
    res = engine.search("machne lerning")
    assert res["total"] == 0
    assert res["did_you_mean"] == "machine learning"


def test_no_did_you_mean_for_good_queries(engine):
    assert engine.search("python")["did_you_mean"] is None


def test_stopword_query(engine):
    res = engine.search("the and of")
    assert res["total"] == 0 and len(res["warnings"]) == 3


def test_result_fields_and_highlighting(engine, corpus):
    r = engine.search("machine learning")["results"][0]
    assert r["id"] == corpus["ml"] and r["rank"] == 1
    assert r["url"] == "https://learn.example.com/ml" and r["domain"] == "learn.example.com"
    assert r["author"] == "Girish" and r["indexed_at"] and r["created_at"]
    assert any(s["highlight"] for s in r["snippet"]["segments"])
    assert r["title_segments"][0] == {"text": "Machine", "highlight": True}
    assert r["matched_terms"] == ["learning", "machine"]
    assert r["score"] > 0 and "explain" not in r


def test_explain(engine):
    r = engine.search("python", explain=True)["results"][0]
    assert r["explain"] and {"tf", "df", "idf", "score"} <= set(r["explain"][0])


def test_rankers_return_same_set_different_scores(engine):
    a, b = engine.search("python", ranker="bm25"), engine.search("python", ranker="tfidf")
    assert set(ids(a)) == set(ids(b))
    assert [r["score"] for r in a["results"]] != [r["score"] for r in b["results"]]


def test_pagination(engine):
    full = ids(engine.search("python OR london OR database", page_size=50))
    p1 = engine.search("python OR london OR database", page_size=2, page=1)
    p2 = engine.search("python OR london OR database", page_size=2, page=2)
    assert ids(p1) + ids(p2) == full[:4]
    assert p1["pages"] == (len(full) + 1) // 2
    assert engine.search("python", page=99)["results"] == []


def test_invalid_mode(engine):
    with pytest.raises(ValueError):
        engine.search("python", mode="magic")


def test_page_size_capped(engine):
    assert engine.search("python", page_size=10_000)["page_size"] == 50


def test_html_in_content_is_returned_as_text(engine, index_service):
    index_service.ingest(DocumentCreate(url="https://x.example.com/", title="<b>Bold</b> zebra",
                                        content="zebra <script>alert(1)</script>"))
    r = engine.search("zebra")["results"][0]
    assert "<script>" in r["snippet"]["text"]          # shown as literal text, not executed
    assert r["title"] == "<b>Bold</b> zebra"


def test_empty_index(db_path, search_index):
    from app.search.search_engine import SearchEngine

    res = SearchEngine(search_index, db_path).search("anything")
    assert res["total"] == 0 and res["results"] == []


def test_deleted_documents_disappear(engine, index_service, corpus):
    index_service.delete(corpus["ml"])
    assert corpus["ml"] not in ids(engine.search("machine"))
