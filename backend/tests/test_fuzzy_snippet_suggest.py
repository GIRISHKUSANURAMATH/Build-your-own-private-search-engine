"""Phase 8: edit distance, snippets and autocomplete."""

import pytest

from app.indexing.analyzer import DEFAULT_ANALYZER as A
from app.search.fuzzy import edit_distance, find_similar, max_distance_for
from app.search.snippet import highlight, make_snippet
from app.search.suggest import Suggester


# ---------------------------------------------------------------- edit distance
@pytest.mark.parametrize(
    "a, b, d",
    [
        ("machine", "machine", 0),
        ("machne", "machine", 1),      # insertion
        ("machiine", "machine", 1),    # deletion
        ("machime", "machine", 1),     # substitution
        ("mahcine", "machine", 1),     # transposition (Damerau)
        ("kitten", "sitting", 3),
        ("", "abc", 3),
        ("abc", "", 3),
        ("lerning", "learning", 1),
        ("teh", "the", 1),
    ],
)
def test_edit_distance(a, b, d):
    assert edit_distance(a, b) == d
    assert edit_distance(b, a) == d   # symmetric


def test_edit_distance_early_exit():
    assert edit_distance("machine", "zzzzzzzzzzzz", max_dist=2) == 3
    assert edit_distance("kitten", "sitting", max_dist=1) == 2


@pytest.mark.parametrize("word, m", [("cat", 0), ("data", 1), ("python", 1), ("machine", 2), ("backpropagation", 2)])
def test_allowed_distance_grows_with_length(word, m):
    assert max_distance_for(word) == m


def test_find_similar_orders_by_distance_then_popularity():
    pops = {"machine": 10, "machines": 3, "marine": 5, "medicine": 1}
    out = find_similar("machne", pops.keys(), lambda w: pops.get(w, 0))
    assert out[0] == ("machine", 1)
    assert [w for w, _ in out] == ["machine"]  # 'machines' is distance 2 > allowed 1 for a 6-letter word


def test_find_similar_skips_dead_words_and_short_words():
    assert find_similar("machne", ["machine"], lambda w: 0) == []
    assert find_similar("cat", ["bat", "car"], lambda w: 5) == []


# ---------------------------------------------------------------- snippets
LONG = ("Intro text about nothing in particular. " * 6 +
        "Here machine learning appears with deep learning methods. " +
        "Filler sentence number two. " * 6 + "The end.")


def test_snippet_picks_window_with_most_distinct_terms():
    s = make_snippet(LONG, {"machin", "learn"}, A)
    assert "machine learning" in s.text
    assert s.text.startswith("… ") and s.text.endswith(" …")
    highlighted = [seg["text"] for seg in s.segments if seg["highlight"]]
    assert "machine" in highlighted and "learning" in highlighted


def test_snippet_segments_reassemble_to_text():
    s = make_snippet(LONG, {"learn"}, A)
    assert "".join(seg["text"] for seg in s.segments) == s.text


def test_snippet_without_matches_uses_start_of_text():
    s = make_snippet(LONG, {"zebra"}, A)
    assert s.text.startswith("Intro text") and all(not seg["highlight"] for seg in s.segments)


def test_snippet_short_text_no_ellipsis():
    s = make_snippet("Python is great.", {"python"}, A)
    assert s.text == "Python is great."
    assert s.segments[0] == {"text": "Python", "highlight": True}


def test_snippet_never_returns_html_markup():
    evil = 'Python <script>alert(1)</script> <img src=x onerror=alert(2)> rocks'
    s = make_snippet(evil, {"python"}, A)
    # the text is returned verbatim as data; nothing is turned into tags like <mark>
    assert "<mark>" not in s.text
    assert all(set(seg) == {"text", "highlight"} for seg in s.segments)


def test_snippet_empty_text():
    assert make_snippet("", {"python"}, A).text == ""


def test_highlight_title():
    segs = highlight("Machine Learning Tutorial", {"machin"}, A)
    assert segs == [{"text": "Machine", "highlight": True}, {"text": " Learning Tutorial", "highlight": False}]


# ---------------------------------------------------------------- autocomplete
def test_complete_word_by_prefix_and_popularity(search_index, db_path, corpus):
    sug = Suggester(search_index, db_path)
    assert sug.complete_word("mach")[0] == "machine"
    assert sug.complete_word("zzz") == []
    assert sug.complete_word("") == []


def test_suggest_completes_last_word(search_index, db_path, corpus):
    out = Suggester(search_index, db_path).suggest("machine le")
    assert "machine learning" in out or "machine learn" in out


def test_suggest_uses_history(search_index, db_path, corpus):
    from app.services.history_service import HistoryService

    h = HistoryService(db_path)
    h.record("machine learning tutorial", "basic", "bm25", 3, 1.0)
    h.record("machine learning tutorial", "basic", "bm25", 3, 1.0)
    h.record("machine nothing", "basic", "bm25", 0, 1.0)   # zero results: never suggested
    out = Suggester(search_index, db_path).suggest("mach")
    assert out[0] == "machine learning tutorial"
    assert "machine nothing" not in out


def test_suggest_escapes_like_wildcards(search_index, db_path, corpus):
    from app.services.history_service import HistoryService

    HistoryService(db_path).record("abc", "basic", "bm25", 1, 1.0)
    assert Suggester(search_index, db_path).suggest("%") == []
    assert Suggester(search_index, db_path).suggest("_bc") == []


def test_suggest_cache_refreshes_after_indexing(search_index, db_path, index_service, corpus):
    from app.services.document_service import DocumentCreate

    sug = Suggester(search_index, db_path)
    assert sug.complete_word("zebr") == []
    index_service.ingest(DocumentCreate(url="https://z.example.com/", content="zebras everywhere"))
    assert sug.complete_word("zebr") == ["zebras"]
