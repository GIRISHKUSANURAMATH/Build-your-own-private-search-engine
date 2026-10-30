"""
The search engine: query text -> ranked, snippeted results.

    query ─► parse_query()  ─► AST
              │
              ▼
          evaluate AST  ─► candidate document ids      (Boolean retrieval: set operations
              │              + list of scoring terms     on postings lists)
              ▼
          Ranker (BM25 / TF-IDF) ─► scores               (ranked retrieval)
              ▼
          paginate ─► load documents ─► snippets + highlighting

BOOLEAN RETRIEVAL decides WHICH documents match:
    AND = intersection of postings lists   python ∩ django
    OR  = union                            python ∪ java
    NOT = difference                       python − java
RANKING decides the ORDER.

PHRASE SEARCH uses positions. For "machine learning" the analyzer gives
machin@0, learn@1. A document matches if, for some position p, "machin" occurs
at p AND "learn" occurs at p+1. Gaps from removed stop words are kept:
"university of london" requires london exactly 2 positions after univers.
"""

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from app.database.database import get_connection
from app.indexing.analyzer import Analyzer
from app.indexing.indexer import SearchIndex
from app.indexing.inverted_index import InvertedIndex
from app.ranking.ranker import QueryTerm, make_ranker
from app.search.fuzzy import find_similar
from app.search.query_parser import And, Filter, Node, Not, Or, Phrase, Term, describe, parse_query
from app.search.snippet import highlight, make_snippet

MODES = ("basic", "boolean", "phrase", "fuzzy")
FUZZY_WEIGHT = 0.8   # fuzzy expansions count a bit less than exact matches
MAX_PAGE_SIZE = 50


@dataclass
class _EvalState:
    scoring: dict[tuple[str, str | None], QueryTerm] = field(default_factory=dict)
    term_words: dict[str, str] = field(default_factory=dict)      # term -> word the user typed
    expansions: dict[str, list[str]] = field(default_factory=dict)
    unknown: list[str] = field(default_factory=list)               # words with no matches (for "did you mean")
    warnings: list[str] = field(default_factory=list)

    def add_scoring(self, term: str, fld: str | None, word: str, weight: float = 1.0) -> None:
        key = (term, fld)
        if key not in self.scoring or self.scoring[key].weight < weight:
            self.scoring[key] = QueryTerm(term, fld, weight)
        self.term_words.setdefault(term, word)


class SearchEngine:
    def __init__(self, index: SearchIndex, db_path: Path):
        self.index = index
        self.db_path = db_path

    @property
    def analyzer(self) -> Analyzer:
        return self.index.analyzer

    # ------------------------------------------------------------ evaluation
    def _field_indexes(self, fld: str | None) -> list[InvertedIndex]:
        return [self.index.title] if fld == "title" else [self.index.body, self.index.title]

    def _phrase_docs(self, terms_offsets: list[tuple[str, int]], fld: str | None) -> set[int]:
        found: set[int] = set()
        for idx in self._field_indexes(fld):
            plists = [idx.get(t) for t, _ in terms_offsets]
            if any(not p for p in plists):
                continue
            candidates = set.intersection(*(set(p) for p in sorted(plists, key=len)))
            for doc in candidates:
                position_sets = [set(p[doc].positions) for p in plists]
                first_positions = plists[0][doc].positions
                if any(all((start + off) in position_sets[k] for k, (_, off) in enumerate(terms_offsets))
                       for start in first_positions):
                    found.add(doc)
        return found

    def _eval(self, node: Node, st: _EvalState, negated: bool = False) -> set[int] | None:
        """Return matching doc ids, or None for 'no constraint' (e.g. only stop words)."""
        match node:
            case Term(word=word, field=fld, fuzzy=fuzzy):
                tokens = self.analyzer.analyze(word)
                if not tokens:
                    st.warnings.append(f"'{word}' was ignored (too common to search for)")
                    return None
                if len(tokens) > 1:  # e.g. "node.js" -> node js : treat as a phrase
                    return self._eval(Phrase(word, fld), st, negated)
                tok = tokens[0]
                docs: set[int] = set()
                for idx in self._field_indexes(fld):
                    docs |= idx.docs(tok.term)
                if not negated:
                    st.add_scoring(tok.term, fld, tok.normalized)
                if fuzzy:
                    similar = find_similar(tok.normalized, self.index.vocabulary.keys(), self.index.popularity)
                    if similar:
                        st.expansions[tok.normalized] = [w for w, _ in similar]
                    for w, _ in similar:
                        term = self.index.vocabulary[w]
                        for idx in self._field_indexes(fld):
                            docs |= idx.docs(term)
                        if not negated:
                            st.add_scoring(term, fld, w, FUZZY_WEIGHT)
                elif not docs and not negated:
                    st.unknown.append(tok.normalized)
                return docs

            case Phrase(text=text, field=fld):
                tokens = self.analyzer.analyze(text)
                if not tokens:
                    st.warnings.append(f'"{text}" contains only stop words and was ignored')
                    return None
                if len(tokens) == 1:
                    return self._eval(Term(tokens[0].original, fld), st, negated)
                base = tokens[0].position
                terms_offsets = [(t.term, t.position - base) for t in tokens]
                if not negated:
                    for t in tokens:
                        st.add_scoring(t.term, fld, t.normalized)
                return self._phrase_docs(terms_offsets, fld)

            case Filter(field="site", value=value):
                host = urlsplit(value).hostname if "://" in value else value.split("/")[0]
                host = (host or "").lower().strip(".")
                return {d for d, m in self.index.doc_meta.items()
                        if m.domain == host or m.domain.endswith("." + host)}

            case Filter(field="author", value=value):
                wanted = set(self.analyzer.terms(value))
                if not wanted:
                    return None
                return {d for d, m in self.index.doc_meta.items() if wanted <= m.author_terms}

            case Not(child=child):
                inner = self._eval(child, st, negated=not negated)
                return None if inner is None else self.index.all_doc_ids() - inner

            case And(children=children):
                positives = [self._eval(c, st, negated) for c in children if not isinstance(c, Not)]
                negatives = [self._eval(c.child, st, not negated) for c in children if isinstance(c, Not)]
                sets = [s for s in positives if s is not None]
                if sets:
                    result = set.intersection(*sorted(sets, key=len))
                elif any(n is not None for n in negatives):
                    result = self.index.all_doc_ids()   # "NOT java" alone: everything except java
                else:
                    return None
                for n in negatives:
                    if n is not None:
                        result -= n
                return result

            case Or(children=children):
                sets = [s for s in (self._eval(c, st, negated) for c in children) if s is not None]
                return set().union(*sets) if sets else None
        return None

    # ------------------------------------------------------------ did you mean
    def _did_you_mean(self, query: str, unknown: list[str]) -> str | None:
        suggestion = query
        changed = False
        for word in unknown:
            similar = find_similar(word, self.index.vocabulary.keys(), self.index.popularity, limit=1)
            if similar:
                best = similar[0][0]
                suggestion, n = re.subn(rf"(?i)\b{re.escape(word)}\b", best, suggestion, count=1)
                changed = changed or n > 0
        return suggestion if changed and suggestion != query else None

    # ------------------------------------------------------------ public API
    def search(self, query: str, *, mode: str = "basic", ranker: str = "bm25", page: int = 1,
               page_size: int = 10, explain: bool = False) -> dict:
        started = time.perf_counter()
        if mode not in MODES:
            raise ValueError(f"Unknown mode {mode!r}; choose from {MODES}")
        page = max(1, page)
        page_size = max(1, min(page_size, MAX_PAGE_SIZE))

        parsed = parse_query(query, mode)
        st = _EvalState(warnings=list(parsed.warnings))
        with self.index.lock:
            candidates = self._eval(parsed.root, st) if parsed.root is not None else None
            if candidates is None:
                candidates = set()
                if parsed.root is not None and not st.warnings:
                    st.warnings.append("Nothing searchable in the query")
            scorer = make_ranker(ranker, self.index.fields)
            scored = scorer.score(candidates, list(st.scoring.values()), explain=explain)
            did_you_mean = self._did_you_mean(query, st.unknown) if (st.unknown and len(scored) < 5) else None

        total = len(scored)
        page_items = scored[(page - 1) * page_size: page * page_size]
        results = self._build_results(page_items, set(t for t, _ in st.scoring), st.term_words, explain)
        return {
            "query": query,
            "mode": mode,
            "ranker": ranker,
            "parsed": describe(parsed.root),
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
            "results": results,
            "did_you_mean": did_you_mean,
            "expansions": st.expansions,
            "warnings": st.warnings,
            "took_ms": round((time.perf_counter() - started) * 1000, 2),
        }

    def _build_results(self, items, query_terms: set[str], term_words: dict[str, str], explain: bool) -> list[dict]:
        if not items:
            return []
        ids = [it.doc_id for it in items]
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                f"""SELECT d.id, d.url, d.title, d.content, d.domain, d.author, d.language,
                           d.created_at, d.updated_at, s.indexed_at
                    FROM documents d LEFT JOIN doc_stats s ON s.doc_id = d.id
                    WHERE d.id IN ({','.join('?' * len(ids))})""",
                ids,
            ).fetchall()
        by_id = {r["id"]: r for r in rows}
        results = []
        for rank, it in enumerate(items, 1):
            r = by_id.get(it.doc_id)
            if r is None:  # deleted between ranking and loading
                continue
            title = r["title"] or r["url"]
            snippet = make_snippet(r["content"], query_terms, self.analyzer)
            item = {
                "id": r["id"],
                "rank": rank,
                "title": title,
                "title_segments": highlight(title, query_terms, self.analyzer),
                "url": r["url"],
                "domain": r["domain"],
                "author": r["author"],
                "language": r["language"],
                "snippet": snippet.as_dict(),
                "score": round(it.score, 4),
                "matched_terms": sorted(term_words.get(t, t) for t in it.matched_terms),
                "created_at": r["created_at"],
                "indexed_at": r["indexed_at"],
            }
            if explain:
                item["explain"] = it.breakdown
            results.append(item)
        return results
