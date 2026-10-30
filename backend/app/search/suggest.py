"""
Autocomplete: suggestions while the user types.

Two sources, no AI:
1. PREVIOUS SEARCHES that start with what was typed and returned results,
   most frequent first ("mach" -> "machine learning algorithms").
2. INDEXED WORDS that complete the LAST word being typed, ranked by how many
   documents contain them ("machine le" -> "machine learning", "machine lexer").

Finding all words with a prefix: the vocabulary is kept as a SORTED list, so
every word starting with "mach" sits in one contiguous block. Binary search
(bisect) finds the block's start in O(log n), instead of checking every word.
The sorted list is rebuilt only when the index changes (index.version).
"""

import bisect
from pathlib import Path

from app.database.database import get_connection
from app.indexing.indexer import SearchIndex
from app.indexing.normalizer import normalize_token

MAX_SUGGESTIONS = 8


class Suggester:
    def __init__(self, index: SearchIndex, db_path: Path):
        self.index = index
        self.db_path = db_path
        self._sorted: list[str] = []
        self._version = -1

    def _words(self) -> list[str]:
        if self._version != self.index.version:
            with self.index.lock:
                self._sorted = sorted(self.index.vocabulary)
                self._version = self.index.version
        return self._sorted

    def complete_word(self, prefix: str, limit: int = MAX_SUGGESTIONS) -> list[str]:
        if not prefix:
            return []
        words = self._words()
        start = bisect.bisect_left(words, prefix)
        matches = []
        for w in words[start:]:
            if not w.startswith(prefix):
                break
            pop = self.index.popularity(w)
            if pop > 0:
                matches.append((-pop, w))
        matches.sort()
        return [w for _, w in matches[:limit]]

    def _history(self, typed: str, limit: int) -> list[str]:
        escaped = typed.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                r"""SELECT lower(query) AS q, COUNT(*) AS c, MAX(created_at) AS last
                    FROM search_history
                    WHERE lower(query) LIKE ? ESCAPE '\' AND results_count > 0
                    GROUP BY lower(query) ORDER BY c DESC, last DESC LIMIT ?""",
                (escaped + "%", limit),
            ).fetchall()
        return [r["q"] for r in rows]

    def suggest(self, text: str, limit: int = MAX_SUGGESTIONS) -> list[str]:
        typed = " ".join(text.lower().split())
        if not typed:
            return []
        out: list[str] = []
        for q in self._history(typed, limit):
            if q != typed and q not in out:
                out.append(q)
        head, _, last = typed.rpartition(" ")
        last_norm = normalize_token(last)
        if last_norm:
            for w in self.complete_word(last_norm, limit):
                candidate = f"{head} {w}".strip()
                if candidate != typed and candidate not in out:
                    out.append(candidate)
        return out[:limit]
