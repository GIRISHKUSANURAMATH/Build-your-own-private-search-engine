"""
The Analyzer: the complete text-processing pipeline, from text to index terms.

    text
     │  tokenize()          "Python's  powerful  programming"  ->  raw tokens + offsets
     ▼
    raw tokens
     │  normalize_token()   Python's -> python, powerful -> powerful
     ▼
    normalized tokens
     │  stop-word filter    drop "the", "is", "of"...      (position numbers kept)
     ▼
    content tokens
     │  stem()              programming -> program, powerful -> power
     ▼
    index terms             what Phase 4 will put in the inverted index

THE GOLDEN RULE
---------------
Documents and queries MUST go through the same Analyzer. If the index holds
"program" (stemmed) but the query looks up "programming" (unstemmed), nothing
matches. Phase 5's query parser will call this same class.

WHY TOKENIZE BEFORE NORMALIZING
-------------------------------
The roadmap lists normalization before tokenization, and the result is the
same — but normalizing first would change string lengths (NFKC, 'ß' -> 'ss'),
so token offsets would no longer point into the stored text. Tokenizing the
cleaned text first and normalizing each token keeps offsets exact, which
snippet highlighting needs later.

POSITIONS
---------
`position` counts every token in the text, including stop words that are then
dropped:   "university of london"  ->  univers@0, london@2
Keeping the gap means phrase search (Phase 8) can require "london" to be
exactly 2 positions after "university", which is what the phrase says.

TWO TOKEN STREAMS
-----------------
Each Token keeps `original` and `normalized` alongside the stemmed `term`.
The index stores `term`; fuzzy matching (Phase 8) needs real words
("machine", not the stem "machin"), and snippets need the original text.
"""

from dataclasses import asdict, dataclass

from app.indexing.normalizer import normalize_token
from app.indexing.stemmer import stem as porter_stem
from app.indexing.stopwords import STOPWORDS
from app.indexing.tokenizer import tokenize

MAX_TOKEN_LENGTH = 64  # longer "words" are almost always junk: base64, minified code, hashes


@dataclass(frozen=True, slots=True)
class Token:
    term: str        # final index term (normalized, stemmed)
    normalized: str  # normalized but NOT stemmed — a real word, for fuzzy matching
    original: str    # exactly as in the text
    position: int    # word position in the text (stop words counted)
    start: int       # character offsets into the text: text[start:end] == original
    end: int


@dataclass(frozen=True, slots=True)
class TokenTrace:
    """One token's journey through the pipeline (for /analyze and teaching)."""
    original: str
    normalized: str
    term: str | None       # None when the token was dropped
    position: int
    start: int
    end: int
    dropped: str | None    # None, "stopword", "too_long" or "empty"


class Analyzer:
    def __init__(
        self,
        *,
        lowercase_and_fold: bool = True,
        remove_stopwords: bool = True,
        stem: bool = True,
        stopwords: frozenset[str] = STOPWORDS,
        max_token_length: int = MAX_TOKEN_LENGTH,
    ):
        self.lowercase_and_fold = lowercase_and_fold
        self.remove_stopwords = remove_stopwords
        self.stem = stem
        self.stopwords = stopwords
        self.max_token_length = max_token_length

    def trace(self, text: str) -> list[TokenTrace]:
        """Run the pipeline, keeping every token and why it was kept or dropped."""
        traces: list[TokenTrace] = []
        for position, raw in enumerate(tokenize(text)):
            normalized = normalize_token(raw.text) if self.lowercase_and_fold else raw.text
            dropped: str | None = None
            if not normalized:
                dropped = "empty"
            elif len(normalized) > self.max_token_length:
                dropped = "too_long"
            elif self.remove_stopwords and normalized in self.stopwords:
                dropped = "stopword"
            term = None
            if dropped is None:
                term = porter_stem(normalized) if self.stem else normalized
            traces.append(TokenTrace(raw.text, normalized, term, position, raw.start, raw.end, dropped))
        return traces

    def analyze(self, text: str) -> list[Token]:
        """Tokens that survive the pipeline, in order, with positions and offsets."""
        return [
            Token(t.term, t.normalized, t.original, t.position, t.start, t.end)
            for t in self.trace(text)
            if t.term is not None
        ]

    def terms(self, text: str) -> list[str]:
        """Just the index terms: 'Python is a powerful language' -> ['python', 'power', 'languag']."""
        return [t.term for t in self.analyze(text)]

    def config(self) -> dict:
        return {
            "lowercase_and_fold": self.lowercase_and_fold,
            "remove_stopwords": self.remove_stopwords,
            "stem": self.stem,
            "max_token_length": self.max_token_length,
            "stopword_count": len(self.stopwords),
        }


DEFAULT_ANALYZER = Analyzer()


def analyze(text: str) -> list[Token]:
    return DEFAULT_ANALYZER.analyze(text)


def trace_as_dicts(traces: list[TokenTrace]) -> list[dict]:
    return [asdict(t) for t in traces]
