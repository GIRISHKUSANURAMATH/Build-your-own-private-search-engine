"""Phases 6-7: TF-IDF and BM25, implemented by hand."""

import math

import pytest

from app.indexing.inverted_index import InvertedIndex
from app.ranking import bm25, tfidf
from app.ranking.ranker import Bm25Ranker, QueryTerm, TfIdfRanker, make_ranker


# ---------------------------------------------------------------- TF-IDF
def test_idf_formula():
    assert tfidf.idf(100, 10) == pytest.approx(math.log(10))
    assert tfidf.idf(100, 1) == pytest.approx(math.log(100))


def test_idf_zero_for_term_in_every_document():
    assert tfidf.idf(50, 50) == 0.0


def test_idf_handles_missing_term():
    assert tfidf.idf(10, 0) == 0.0 and tfidf.idf(0, 0) == 0.0


def test_tfidf_is_tf_times_idf():
    assert tfidf.tfidf(3, 10, 100) == pytest.approx(3 * math.log(10))


# ---------------------------------------------------------------- BM25
def test_bm25_idf_never_negative():
    assert bm25.idf(10, 9) > 0           # classic BM25 IDF would be negative here
    assert bm25.idf(10, 10) > 0
    assert bm25.idf(10, 1) > bm25.idf(10, 5)


def test_bm25_hand_computed():
    # N=4, df=1, tf=2, |d|=10, avgdl=8, k1=1.2, b=0.75
    idf = math.log(1 + (4 - 1 + 0.5) / (1 + 0.5))
    norm = 1 - 0.75 + 0.75 * 10 / 8
    expected = idf * (2 * 2.2) / (2 + 1.2 * norm)
    assert bm25.bm25(2, 1, 4, 10, 8.0) == pytest.approx(expected)


def test_bm25_tf_saturates():
    scores = [bm25.tf_component(tf, 10, 10.0) for tf in (1, 2, 5, 10, 50, 1000)]
    assert scores == sorted(scores)                     # more mentions never hurt...
    assert scores[-1] < 1.2 + 1                         # ...but never exceed k1 + 1
    assert scores[1] - scores[0] > scores[-1] - scores[-2] * 0.999  # early mentions matter most
    assert tfidf.tfidf(1000, 1, 10) / tfidf.tfidf(1, 1, 10) == pytest.approx(1000)  # TF-IDF: no saturation


def test_bm25_length_normalisation():
    short = bm25.tf_component(2, doc_len=5, avg_len=10.0)
    long_ = bm25.tf_component(2, doc_len=40, avg_len=10.0)
    assert short > long_
    assert bm25.tf_component(2, 5, 10.0, b=0) == bm25.tf_component(2, 40, 10.0, b=0)  # b=0 ignores length


def test_bm25_zero_tf():
    assert bm25.tf_component(0, 10, 10.0) == 0.0


# ---------------------------------------------------------------- Ranker
def _fields():
    body, title = InvertedIndex("body"), InvertedIndex("title")
    # doc 1: short, mentions python once. doc 2: long, mentions python 5 times.
    body.add_document(1, {"python": [0], "guid": [1]}, length=4)
    body.add_document(2, {"python": [0, 10, 20, 30, 40], "misc": [5]}, length=60)
    body.add_document(3, {"java": [0]}, length=4)
    title.add_document(1, {"python": [0]}, length=2)
    title.add_document(2, {"notes": [0]}, length=1)
    title.add_document(3, {"java": [0]}, length=1)
    return {"body": body, "title": title}


def test_tfidf_prefers_repetition_bm25_prefers_focus():
    f = _fields()
    terms = [QueryTerm("python")]
    tf_order = [r.doc_id for r in TfIdfRanker(f, title_boost=0).score({1, 2}, terms)]
    bm_order = [r.doc_id for r in Bm25Ranker(f, title_boost=0).score({1, 2}, terms)]
    assert tf_order == [2, 1]   # TF-IDF: 5 mentions beat 1, regardless of length
    assert bm_order == [1, 2]   # BM25: saturation + length normalisation favour the short focused doc


def test_title_boost_counts():
    f = _fields()
    no_boost = Bm25Ranker(f, title_boost=0).score({1}, [QueryTerm("python")])[0].score
    boosted = Bm25Ranker(f, title_boost=2).score({1}, [QueryTerm("python")])[0].score
    assert boosted > no_boost


def test_title_field_terms_score_title_only():
    f = _fields()
    r = Bm25Ranker(f).score({1, 2}, [QueryTerm("python", field="title")], explain=True)
    assert r[0].doc_id == 1 and r[1].score == 0
    assert all(e["field"] == "title" for e in r[0].breakdown)


def test_explain_breakdown_sums_to_score():
    f = _fields()
    for ranker in (Bm25Ranker(f), TfIdfRanker(f)):
        (r,) = ranker.score({1}, [QueryTerm("python"), QueryTerm("guid")], explain=True)
        assert sum(e["score"] for e in r.breakdown) == pytest.approx(r.score, abs=1e-3)
        assert {"term", "field", "tf", "df", "N", "idf"} <= set(r.breakdown[0])


def test_matched_terms_and_unmatched_docs():
    f = _fields()
    res = {r.doc_id: r for r in Bm25Ranker(f).score({1, 3}, [QueryTerm("python")])}
    assert res[1].matched_terms == {"python"} and res[3].score == 0 and res[3].matched_terms == set()


def test_fuzzy_weight_reduces_contribution():
    f = _fields()
    full = Bm25Ranker(f).score({1}, [QueryTerm("python")])[0].score
    weak = Bm25Ranker(f).score({1}, [QueryTerm("python", weight=0.5)])[0].score
    assert weak == pytest.approx(full / 2)


def test_ties_sorted_by_newest_doc():
    f = _fields()
    assert [r.doc_id for r in Bm25Ranker(f).score({1, 2, 3}, [])] == [3, 2, 1]


def test_make_ranker():
    f = _fields()
    assert make_ranker("bm25", f).name == "bm25"
    assert make_ranker("tfidf", f).name == "tfidf"
    with pytest.raises(ValueError):
        make_ranker("pagerank", f)
