"""Phase 4: inverted index, forward index, indexer persistence."""

import sqlite3
from array import array

import pytest

from app.database.database import get_connection
from app.indexing.analyzer import Analyzer
from app.indexing.forward_index import ForwardIndex
from app.indexing.indexer import SearchIndex
from app.indexing.inverted_index import InvertedIndex, Posting
from app.services.document_service import DocumentCreate


# ---------------------------------------------------------------- data structures
def test_inverted_index_add_and_query():
    idx = InvertedIndex("body")
    idx.add_document(1, {"python": [0, 5], "fast": [2]}, length=3)
    idx.add_document(2, {"python": [1]}, length=1)
    assert idx.docs("python") == {1, 2}
    assert idx.df("python") == 2 and idx.df("fast") == 1 and idx.df("missing") == 0
    assert idx.tf("python", 1) == 2 and idx.tf("python", 3) == 0
    assert list(idx.get("python")[1].positions) == [0, 5]
    assert idx.doc_count == 2 and idx.avg_length == 2.0 and idx.term_count == 2 and idx.posting_count == 3


def test_inverted_index_rejects_double_add():
    idx = InvertedIndex("body")
    idx.add_document(1, {"a": [0]}, 1)
    with pytest.raises(ValueError):
        idx.add_document(1, {"a": [0]}, 1)


def test_inverted_index_remove_cleans_empty_postings():
    idx = InvertedIndex("body")
    idx.add_document(1, {"python": [0], "rare": [1]}, 2)
    idx.add_document(2, {"python": [0]}, 1)
    idx.remove_document(1, {"python", "rare"})
    assert idx.docs("python") == {2}
    assert "rare" not in idx.postings
    assert idx.total_length == 1 and idx.doc_count == 1


def test_positions_blob_roundtrip():
    p = Posting(1, 3, array("I", [0, 7, 70000]))
    assert list(Posting.positions_from_bytes(p.to_bytes())) == [0, 7, 70000]
    assert len(p.to_bytes()) == 12  # 4 bytes per position


def test_forward_index():
    fwd = ForwardIndex()
    fwd.add(1, "body", {"python": 3, "fast": 1, "code": 3})
    assert fwd.terms(1, "body") == {"python", "fast", "code"}
    assert fwd.top_terms(1, 2) == [("code", 3), ("python", 3)]  # ties broken alphabetically
    assert 1 in fwd and len(fwd) == 1
    fwd.remove(1)
    assert 1 not in fwd and fwd.terms(1, "body") == set()


# ---------------------------------------------------------------- indexer
def test_index_document_builds_both_fields(search_index, corpus):
    ml = corpus["ml"]
    assert search_index.is_indexed(ml)
    assert ml in search_index.body.docs("machin")
    assert ml in search_index.title.docs("tutori")
    assert search_index.body.tf("machin", ml) == 2
    # positions point at the right words: "Machine learning is a field..."
    assert list(search_index.body.get("machin")[ml].positions) == [0, 9]
    assert search_index.forward.term_frequencies(ml)["learn"] == 3
    assert search_index.doc_meta[ml].domain == "learn.example.com"
    assert search_index.doc_meta[ml].author_terms == {"girish"}


def test_vocabulary_maps_real_words_to_terms(search_index, corpus):
    assert search_index.vocabulary["machine"] == "machin"
    assert search_index.vocabulary["learning"] == "learn"
    assert search_index.popularity("machine") >= 1
    assert search_index.popularity("nonexistent") == 0


def test_index_persists_and_reloads_identically(db_path, search_index, corpus):
    fresh = SearchIndex()
    fresh.load(db_path)
    assert fresh.stats() == search_index.stats()
    for term, plist in search_index.body.postings.items():
        assert {d: (p.tf, list(p.positions)) for d, p in plist.items()} == \
               {d: (p.tf, list(p.positions)) for d, p in fresh.body.get(term).items()}
    assert fresh.vocabulary == search_index.vocabulary
    assert fresh.doc_meta == search_index.doc_meta


def test_df_column_matches_memory(db_path, search_index, corpus):
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT field, term, df FROM terms WHERE df > 0").fetchall()
    for field, term, df in rows:
        assert search_index.fields[field].df(term) == df


def test_reindex_after_content_change(db_path, search_index, index_service, corpus):
    doc_id = corpus["python"]
    index_service.documents.update_document(doc_id, content="Rust replaced everything.")
    assert search_index.index_pending(db_path)["indexed"] == 1
    assert doc_id not in search_index.body.docs("python")
    assert doc_id in search_index.body.docs("rust")
    with get_connection(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM postings WHERE doc_id = ?", (doc_id,)).fetchone()[0] == \
            len(search_index.forward.terms(doc_id, "body")) + len(search_index.forward.terms(doc_id, "title"))


def test_index_pending_is_noop_when_up_to_date(db_path, search_index, corpus):
    assert search_index.index_pending(db_path)["indexed"] == 0


def test_remove_document(db_path, search_index, corpus):
    doc_id = corpus["db"]
    assert search_index.remove_document(db_path, doc_id) is True
    assert not search_index.is_indexed(doc_id)
    assert search_index.body.docs("databas") == set()
    assert search_index.remove_document(db_path, doc_id) is False
    with get_connection(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM postings WHERE doc_id = ?", (doc_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT df FROM terms WHERE field='body' AND term='databas'").fetchone()[0] == 0


def test_deleting_document_row_cascades_postings(db_path, search_index, index_service, corpus):
    index_service.delete(corpus["db"])
    with get_connection(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == \
            search_index.body.posting_count + search_index.title.posting_count
        assert conn.execute("SELECT COUNT(*) FROM doc_stats").fetchone()[0] == 4


def test_rebuild_matches_incremental(db_path, search_index, corpus):
    before = search_index.stats()
    result = search_index.rebuild(db_path)
    assert result["indexed"] == 5
    after = search_index.stats()
    for key in ("documents", "terms", "title_terms", "postings", "vocabulary_words", "total_tokens"):
        assert after[key] == before[key]


def test_failed_write_leaves_memory_untouched(db_path, search_index, index_service, corpus, monkeypatch):
    before = search_index.stats()
    doc = index_service.documents.create_document(DocumentCreate(url="https://x.example.com/", content="zebra"))

    def boom(*a, **kw):
        raise sqlite3.OperationalError("disk full (simulated)")

    monkeypatch.setattr(SearchIndex, "_touch_meta", boom)
    with pytest.raises(sqlite3.OperationalError):
        search_index.index_document(db_path, doc)
    assert search_index.stats() == before
    assert search_index.body.docs("zebra") == set()
    with get_connection(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM doc_stats WHERE doc_id = ?", (doc.id,)).fetchone()[0] == 0


def test_needs_rebuild_when_analyzer_changes(db_path, search_index, corpus):
    other = SearchIndex(analyzer=Analyzer(stem=False))
    other.load(db_path)
    assert other.needs_rebuild() is True
    other.rebuild(db_path)
    assert other.needs_rebuild() is False
    assert "programming" in other.body.postings  # unstemmed now


def test_empty_index_stats(search_index):
    s = search_index.stats()
    assert s["documents"] == 0 and s["terms"] == 0 and s["avg_doc_length"] == 0
