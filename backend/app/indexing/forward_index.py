"""
The forward index: document -> its terms (with frequencies).

    doc 1 -> {python: 3, fast: 1, program: 2}
    doc 2 -> {databas: 4, index: 2}

WHY KEEP BOTH DIRECTIONS
------------------------
* The INVERTED index answers queries: "which documents contain python?"
* The FORWARD index answers questions about one document:
    - Deleting or re-indexing document 7: which postings lists must change?
      Without the forward index we would have to scan every postings list.
    - "What are the most important terms of this document?" (document view)
    - Document length and term vector for ranking/analysis.

Search engines usually build the forward index first (while parsing documents)
and then "invert" it into the inverted index.
"""


class ForwardIndex:
    def __init__(self):
        # doc_id -> field -> term -> tf
        self.docs: dict[int, dict[str, dict[str, int]]] = {}

    def add(self, doc_id: int, field: str, term_tf: dict[str, int]) -> None:
        self.docs.setdefault(doc_id, {})[field] = dict(term_tf)

    def add_term(self, doc_id: int, field: str, term: str, tf: int) -> None:
        self.docs.setdefault(doc_id, {}).setdefault(field, {})[term] = tf

    def terms(self, doc_id: int, field: str) -> set[str]:
        return set(self.docs.get(doc_id, {}).get(field, {}))

    def term_frequencies(self, doc_id: int, field: str = "body") -> dict[str, int]:
        return dict(self.docs.get(doc_id, {}).get(field, {}))

    def top_terms(self, doc_id: int, n: int = 10, field: str = "body") -> list[tuple[str, int]]:
        tf = self.docs.get(doc_id, {}).get(field, {})
        return sorted(tf.items(), key=lambda kv: (-kv[1], kv[0]))[:n]

    def remove(self, doc_id: int) -> dict[str, dict[str, int]]:
        return self.docs.pop(doc_id, {})

    def __contains__(self, doc_id: int) -> bool:
        return doc_id in self.docs

    def __len__(self) -> int:
        return len(self.docs)
