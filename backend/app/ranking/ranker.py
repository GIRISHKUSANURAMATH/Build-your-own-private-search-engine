"""
Ranker: scores candidate documents for a set of query terms.

Both algorithms share one interface, so the search engine can switch with
`?ranker=tfidf` / `?ranker=bm25` and the evaluation can compare them on the
same candidates.

FIELDS
------
Each query term is scored in the body AND in the title, and title scores are
multiplied by TITLE_BOOST: a word in the title is a strong signal of what a
page is about. (A simplified version of what real engines do; BM25F is the
full field-aware model.) `title:word` terms are scored on the title only.

Every score can be explained: `explain=True` returns each term's contribution
in each field, which the UI and the viva can show.
"""

from dataclasses import dataclass, field

from app.indexing.inverted_index import InvertedIndex
from app.ranking import bm25 as bm25_mod
from app.ranking import tfidf as tfidf_mod

TITLE_BOOST = 2.0
RANKERS = ("bm25", "tfidf")


@dataclass(frozen=True)
class QueryTerm:
    term: str
    field: str | None = None   # None = body + title; "title" = title only
    weight: float = 1.0        # < 1 for fuzzy expansions


@dataclass
class ScoredDoc:
    doc_id: int
    score: float
    matched_terms: set[str] = field(default_factory=set)
    breakdown: list[dict] = field(default_factory=list)


class Ranker:
    name = "base"

    def __init__(self, fields: dict[str, InvertedIndex], title_boost: float = TITLE_BOOST):
        self.fields = fields
        self.title_boost = title_boost

    def term_score(self, index: InvertedIndex, term: str, doc_id: int) -> tuple[float, dict]:
        raise NotImplementedError

    def score(self, doc_ids: set[int], terms: list[QueryTerm], explain: bool = False) -> list[ScoredDoc]:
        results: list[ScoredDoc] = []
        for doc_id in doc_ids:
            total = 0.0
            matched: set[str] = set()
            breakdown: list[dict] = []
            for qt in terms:
                parts = [("title", self.title_boost)] if qt.field == "title" else [("body", 1.0), ("title", self.title_boost)]
                for field_name, boost in parts:
                    index = self.fields[field_name]
                    if index.tf(qt.term, doc_id) == 0:
                        continue
                    s, detail = self.term_score(index, qt.term, doc_id)
                    contribution = s * boost * qt.weight
                    total += contribution
                    if contribution > 0:
                        matched.add(qt.term)
                    if explain:
                        breakdown.append({
                            "term": qt.term, "field": field_name, "boost": boost, "weight": qt.weight,
                            "score": round(contribution, 4), **detail,
                        })
            results.append(ScoredDoc(doc_id, total, matched, breakdown))
        # highest score first; ties broken by newest document (higher id) for stable output
        results.sort(key=lambda r: (-r.score, -r.doc_id))
        return results


class TfIdfRanker(Ranker):
    name = "tfidf"

    def term_score(self, index: InvertedIndex, term: str, doc_id: int) -> tuple[float, dict]:
        tf, df, n = index.tf(term, doc_id), index.df(term), index.doc_count
        idf = tfidf_mod.idf(n, df)
        return tf * idf, {"tf": tf, "df": df, "N": n, "idf": round(idf, 4)}


class Bm25Ranker(Ranker):
    name = "bm25"

    def __init__(self, fields, title_boost: float = TITLE_BOOST,
                 k1: float = bm25_mod.DEFAULT_K1, b: float = bm25_mod.DEFAULT_B):
        super().__init__(fields, title_boost)
        self.k1, self.b = k1, b

    def term_score(self, index: InvertedIndex, term: str, doc_id: int) -> tuple[float, dict]:
        tf, df, n = index.tf(term, doc_id), index.df(term), index.doc_count
        dl, avgdl = index.doc_lengths.get(doc_id, 0), index.avg_length
        idf = bm25_mod.idf(n, df)
        tfc = bm25_mod.tf_component(tf, dl, avgdl, self.k1, self.b)
        return idf * tfc, {"tf": tf, "df": df, "N": n, "idf": round(idf, 4),
                           "doc_len": dl, "avg_len": round(avgdl, 2), "tf_part": round(tfc, 4)}


def make_ranker(name: str, fields: dict[str, InvertedIndex], **kwargs) -> Ranker:
    if name == "tfidf":
        return TfIdfRanker(fields, **{k: v for k, v in kwargs.items() if k == "title_boost"})
    if name == "bm25":
        return Bm25Ranker(fields, **kwargs)
    raise ValueError(f"Unknown ranker {name!r}; choose from {RANKERS}")
