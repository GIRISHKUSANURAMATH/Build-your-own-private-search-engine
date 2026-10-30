"""
The inverted index: term -> postings list.

WHY "INVERTED"
--------------
Documents naturally map to words (doc 1 contains "python", "fast", ...). That
is the *forward* direction. To answer a query we need the opposite:

    python    -> [doc 1 (tf=3, positions 4,10,21), doc 7 (tf=1, positions 8)]
    database  -> [doc 2 (...), doc 3 (...)]

With this, finding every document that contains "python" is one dictionary
lookup instead of reading every document. Search cost depends on how many
documents contain the query terms, not on how many documents exist.

POSTING = one (term, document) pair:
    doc_id     which document
    tf         term frequency: how many times the term occurs in it
    positions  where (word positions) — needed for phrase search and snippets

`len(postings[term])` is the term's document frequency (df), which IDF uses.
"""

from array import array
from dataclasses import dataclass


@dataclass(slots=True)
class Posting:
    doc_id: int
    tf: int
    positions: array  # array('I') of word positions, ascending

    def to_bytes(self) -> bytes:
        return self.positions.tobytes()

    @staticmethod
    def positions_from_bytes(data: bytes) -> array:
        a = array("I")
        a.frombytes(data)
        return a


class InvertedIndex:
    """Postings for ONE field (body or title), plus document lengths for BM25."""

    def __init__(self, field: str):
        self.field = field
        self.postings: dict[str, dict[int, Posting]] = {}
        self.doc_lengths: dict[int, int] = {}   # number of index terms in each document
        self.total_length = 0

    # ------------------------------------------------------------ building
    def add_document(self, doc_id: int, term_positions: dict[str, list[int]], length: int) -> None:
        """Add one document. term_positions: term -> its word positions in the document."""
        if doc_id in self.doc_lengths:
            raise ValueError(f"document {doc_id} is already in the {self.field} index")
        for term, positions in term_positions.items():
            self.postings.setdefault(term, {})[doc_id] = Posting(doc_id, len(positions), array("I", positions))
        self.doc_lengths[doc_id] = length
        self.total_length += length

    def add_posting(self, term: str, posting: Posting) -> None:
        """Used when loading from the database."""
        self.postings.setdefault(term, {})[posting.doc_id] = posting

    def set_length(self, doc_id: int, length: int) -> None:
        self.total_length += length - self.doc_lengths.get(doc_id, 0)
        self.doc_lengths[doc_id] = length

    def remove_document(self, doc_id: int, terms: set[str]) -> None:
        """Remove a document. `terms` comes from the forward index, so we only
        touch the postings lists that actually contain this document."""
        for term in terms:
            plist = self.postings.get(term)
            if plist is not None:
                plist.pop(doc_id, None)
                if not plist:
                    del self.postings[term]
        self.total_length -= self.doc_lengths.pop(doc_id, 0)

    # ------------------------------------------------------------ querying
    def get(self, term: str) -> dict[int, Posting]:
        return self.postings.get(term, {})

    def docs(self, term: str) -> set[int]:
        return set(self.postings.get(term, ()))

    def df(self, term: str) -> int:
        return len(self.postings.get(term, ()))

    def tf(self, term: str, doc_id: int) -> int:
        p = self.postings.get(term, {}).get(doc_id)
        return p.tf if p else 0

    @property
    def doc_count(self) -> int:
        return len(self.doc_lengths)

    @property
    def avg_length(self) -> float:
        return self.total_length / len(self.doc_lengths) if self.doc_lengths else 0.0

    @property
    def term_count(self) -> int:
        return len(self.postings)

    @property
    def posting_count(self) -> int:
        return sum(len(p) for p in self.postings.values())
