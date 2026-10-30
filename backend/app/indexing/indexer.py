"""
The indexer: keeps the in-memory index and its SQLite copy in sync.

    documents table ──► Analyzer ──► ForwardIndex (doc -> terms)
                                 └─► InvertedIndex x2 (body, title: term -> postings)
                                 └─► vocabulary (word -> term, for fuzzy/autocomplete)
                                         │
                                         ▼  same transaction
                     terms / postings / doc_stats / vocabulary tables

DESIGN
------
* Search reads only memory (dictionary lookups, no SQL joins).
* Every change is written to SQLite in ONE transaction first; memory is updated
  only after the commit succeeds, so the two can never disagree after an error.
* On startup `load()` rebuilds memory from the tables — no re-analysis needed.
* Two fields are indexed separately: `title` and `body`. Ranking boosts title
  matches, and `title:word` queries look only at the title index.
* A lock serialises writers (the crawler runs in a background thread) and
  protects readers from seeing a half-updated index.
"""

import json
import sqlite3
from array import array
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from app.database.database import get_connection
from app.database.models import Document
from app.database.repositories import DocumentRepository
from app.indexing.analyzer import DEFAULT_ANALYZER, Analyzer
from app.indexing.forward_index import ForwardIndex
from app.indexing.inverted_index import InvertedIndex, Posting

FIELDS = ("body", "title")
NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


@dataclass(slots=True)
class DocMeta:
    """Small per-document facts kept in memory for filters (site:, author:)."""
    domain: str
    author_terms: frozenset[str]
    title: str


@dataclass(slots=True)
class _Prepared:
    """A document analyzed but not yet written anywhere."""
    doc_id: int
    content_hash: str
    fields: dict[str, dict[str, list[int]]]   # field -> term -> positions
    lengths: dict[str, int]                   # field -> number of index terms
    words: dict[str, str]                     # normalized word -> term
    meta: DocMeta


class SearchIndex:
    def __init__(self, analyzer: Analyzer = DEFAULT_ANALYZER):
        self.analyzer = analyzer
        self.lock = threading.RLock()
        self._reset()

    def _reset(self) -> None:
        self.fields: dict[str, InvertedIndex] = {f: InvertedIndex(f) for f in FIELDS}
        self.forward = ForwardIndex()
        self.vocabulary: dict[str, str] = {}       # word -> term
        self.doc_meta: dict[int, DocMeta] = {}
        self._term_ids: dict[tuple[str, str], int] = {}
        self.version = getattr(self, "version", 0) + 1  # bumped on every change (cache invalidation)
        self.last_indexed_at: str | None = None
        self.stored_analyzer: str | None = None

    # ------------------------------------------------------------ convenience
    @property
    def body(self) -> InvertedIndex:
        return self.fields["body"]

    @property
    def title(self) -> InvertedIndex:
        return self.fields["title"]

    @property
    def doc_count(self) -> int:
        return len(self.doc_meta)

    def is_indexed(self, doc_id: int) -> bool:
        return doc_id in self.doc_meta

    def all_doc_ids(self) -> set[int]:
        return set(self.doc_meta)

    def analyzer_signature(self) -> str:
        return json.dumps(self.analyzer.config(), sort_keys=True)

    # ------------------------------------------------------------ analysis
    def _prepare(self, doc: Document) -> _Prepared:
        fields: dict[str, dict[str, list[int]]] = {}
        lengths: dict[str, int] = {}
        words: dict[str, str] = {}
        for field, text in (("body", doc.content), ("title", doc.title)):
            positions: dict[str, list[int]] = defaultdict(list)
            tokens = self.analyzer.analyze(text or "")
            for tok in tokens:
                positions[tok.term].append(tok.position)
                if tok.normalized.isalpha():  # only real words feed fuzzy/autocomplete
                    words.setdefault(tok.normalized, tok.term)
            fields[field] = dict(positions)
            lengths[field] = len(tokens)
        author_terms = frozenset(self.analyzer.terms(doc.author or ""))
        return _Prepared(doc.id, doc.content_hash, fields, lengths, words,
                         DocMeta(doc.domain, author_terms, doc.title))

    # ------------------------------------------------------------ SQL helpers
    def _term_id(self, conn: sqlite3.Connection, field: str, term: str, new_ids: dict) -> int:
        key = (field, term)
        tid = self._term_ids.get(key) or new_ids.get(key)
        if tid is None:
            row = conn.execute("SELECT id FROM terms WHERE field = ? AND term = ?", key).fetchone()
            tid = row[0] if row else conn.execute(
                "INSERT INTO terms (field, term, df) VALUES (?, ?, 0)", key).lastrowid
            new_ids[key] = tid
        return tid

    def _write_document(self, conn: sqlite3.Connection, p: _Prepared, new_ids: dict) -> None:
        rows, df_ids = [], []
        for field, term_positions in p.fields.items():
            for term, positions in term_positions.items():
                tid = self._term_id(conn, field, term, new_ids)
                rows.append((tid, p.doc_id, len(positions), array("I", positions).tobytes()))
                df_ids.append((tid,))
        conn.executemany("INSERT INTO postings (term_id, doc_id, tf, positions) VALUES (?, ?, ?, ?)", rows)
        conn.executemany("UPDATE terms SET df = df + 1 WHERE id = ?", df_ids)
        conn.execute(
            f"""INSERT INTO doc_stats (doc_id, body_length, title_length, content_hash, indexed_at)
                VALUES (?, ?, ?, ?, {NOW_SQL})
                ON CONFLICT(doc_id) DO UPDATE SET body_length = excluded.body_length,
                    title_length = excluded.title_length, content_hash = excluded.content_hash,
                    indexed_at = excluded.indexed_at""",
            (p.doc_id, p.lengths["body"], p.lengths["title"], p.content_hash),
        )
        conn.executemany(
            "INSERT INTO vocabulary (word, term) VALUES (?, ?) ON CONFLICT(word) DO UPDATE SET term = excluded.term",
            list(p.words.items()),
        )

    def _delete_document_rows(self, conn: sqlite3.Connection, doc_id: int) -> None:
        conn.execute(
            "UPDATE terms SET df = df - 1 WHERE id IN (SELECT term_id FROM postings WHERE doc_id = ?)", (doc_id,))
        conn.execute("DELETE FROM postings WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM doc_stats WHERE doc_id = ?", (doc_id,))

    def _touch_meta(self, conn: sqlite3.Connection) -> str:
        now = conn.execute(f"SELECT {NOW_SQL}").fetchone()[0]
        conn.executemany(
            "INSERT INTO index_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [("last_indexed_at", now), ("analyzer", self.analyzer_signature())],
        )
        return now

    # ------------------------------------------------------------ memory helpers
    def _apply_add(self, p: _Prepared) -> None:
        for field, term_positions in p.fields.items():
            self.fields[field].add_document(p.doc_id, term_positions, p.lengths[field])
            self.forward.add(p.doc_id, field, {t: len(pos) for t, pos in term_positions.items()})
        self.vocabulary.update(p.words)
        self.doc_meta[p.doc_id] = p.meta

    def _apply_remove(self, doc_id: int) -> None:
        removed = self.forward.remove(doc_id)
        for field in FIELDS:
            self.fields[field].remove_document(doc_id, set(removed.get(field, {})))
        self.doc_meta.pop(doc_id, None)

    # ------------------------------------------------------------ public API
    def load(self, db_path: Path) -> None:
        """Rebuild the in-memory index from the SQLite tables."""
        with self.lock, get_connection(db_path) as conn:
            self._reset()
            id_to_term: dict[int, tuple[str, str]] = {}
            for tid, field, term in conn.execute("SELECT id, field, term FROM terms"):
                id_to_term[tid] = (field, term)
                self._term_ids[(field, term)] = tid
            for tid, doc_id, tf, blob in conn.execute("SELECT term_id, doc_id, tf, positions FROM postings"):
                field, term = id_to_term[tid]
                self.fields[field].add_posting(term, Posting(doc_id, tf, Posting.positions_from_bytes(blob)))
                self.forward.add_term(doc_id, field, term, tf)
            for doc_id, body_len, title_len in conn.execute(
                "SELECT doc_id, body_length, title_length FROM doc_stats"
            ):
                self.body.set_length(doc_id, body_len)
                self.title.set_length(doc_id, title_len)
            for row in conn.execute(
                "SELECT d.id, d.domain, d.author, d.title FROM documents d JOIN doc_stats s ON s.doc_id = d.id"
            ):
                self.doc_meta[row[0]] = DocMeta(row[1], frozenset(self.analyzer.terms(row[2] or "")), row[3])
            self.vocabulary = dict(conn.execute("SELECT word, term FROM vocabulary").fetchall())
            meta = dict(conn.execute("SELECT key, value FROM index_meta").fetchall())
            self.last_indexed_at = meta.get("last_indexed_at")
            self.stored_analyzer = meta.get("analyzer")
            self.version += 1

    def needs_rebuild(self) -> bool:
        """True if the index was built with a different analyzer configuration."""
        return self.stored_analyzer is not None and self.stored_analyzer != self.analyzer_signature()

    def index_document(self, db_path: Path, doc: Document) -> None:
        """Add or re-index one document (atomic: DB and memory both change, or neither)."""
        prepared = self._prepare(doc)
        with self.lock:
            new_ids: dict = {}
            with get_connection(db_path) as conn:
                if self.is_indexed(doc.id):
                    self._delete_document_rows(conn, doc.id)
                self._write_document(conn, prepared, new_ids)
                now = self._touch_meta(conn)
            # committed — now mirror the change in memory
            if self.is_indexed(doc.id):
                self._apply_remove(doc.id)
            self._term_ids.update(new_ids)
            self._apply_add(prepared)
            self.last_indexed_at = now
            self.version += 1

    def remove_document(self, db_path: Path, doc_id: int) -> bool:
        """Remove a document from the index (the document row itself is not touched)."""
        with self.lock:
            if not self.is_indexed(doc_id):
                return False
            with get_connection(db_path) as conn:
                self._delete_document_rows(conn, doc_id)
            self._apply_remove(doc_id)
            self.version += 1
            return True

    def index_pending(self, db_path: Path) -> dict:
        """Index documents that are new or whose content changed since indexing."""
        started = time.perf_counter()
        with get_connection(db_path) as conn:
            rows = conn.execute(
                """SELECT d.id FROM documents d LEFT JOIN doc_stats s ON s.doc_id = d.id
                   WHERE s.doc_id IS NULL OR s.content_hash != d.content_hash ORDER BY d.id"""
            ).fetchall()
            docs = [DocumentRepository(conn).get(r[0]) for r in rows]
        for doc in docs:
            if doc is not None:
                self.index_document(db_path, doc)
        return {"indexed": len(docs), "seconds": round(time.perf_counter() - started, 3)}

    def rebuild(self, db_path: Path) -> dict:
        """Drop the whole index and rebuild it from the documents table, in one transaction."""
        started = time.perf_counter()
        with self.lock:
            with get_connection(db_path) as conn:
                docs = [Document.from_row(r) for r in conn.execute("SELECT * FROM documents ORDER BY id")]
                prepared = [self._prepare(d) for d in docs]
                for table in ("postings", "doc_stats", "terms", "vocabulary"):
                    conn.execute(f"DELETE FROM {table}")
                # Term ids are about to be re-created; drop the id cache (only a
                # cache: if the transaction fails, ids are looked up again).
                self._term_ids = {}
                new_ids: dict = {}
                for p in prepared:
                    self._write_document(conn, p, new_ids)
                now = self._touch_meta(conn)
            # committed — only now replace the in-memory index
            self._reset()
            self._term_ids.update(new_ids)
            for p in prepared:
                self._apply_add(p)
            self.last_indexed_at = now
            self.stored_analyzer = self.analyzer_signature()
            self.version += 1
        return {"indexed": len(prepared), "seconds": round(time.perf_counter() - started, 3)}

    def stats(self) -> dict:
        with self.lock:
            return {
                "documents": self.doc_count,
                "terms": self.body.term_count,
                "title_terms": self.title.term_count,
                "postings": self.body.posting_count + self.title.posting_count,
                "vocabulary_words": len(self.vocabulary),
                "avg_doc_length": round(self.body.avg_length, 1),
                "total_tokens": self.body.total_length,
                "last_indexed_at": self.last_indexed_at,
                "needs_rebuild": self.needs_rebuild(),
            }

    def popularity(self, word: str) -> int:
        """How useful a vocabulary word is as a suggestion: df of its term (0 = gone)."""
        term = self.vocabulary.get(word)
        if term is None:
            return 0
        return self.body.df(term) + self.title.df(term)

