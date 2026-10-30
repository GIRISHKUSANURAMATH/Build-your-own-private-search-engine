"""
Snippet generation: the short excerpt under each search result.

STEPS
-----
1. Re-run the analyzer on the document text. Every token comes back with its
   index term AND its exact character offsets in the text.
2. Mark tokens whose term is one of the query terms.
3. Slide a window of WINDOW tokens over the document and pick the window
   that contains the MOST DIFFERENT query terms (ties: most matches, then
   earliest). A window showing "machine" and "learning" together beats one
   with "machine" three times.
4. Cut the original text at the window's first and last character offsets,
   adding "…" where text was cut.
5. Return it as SEGMENTS: [{"text": "Learn ", "highlight": false},
   {"text": "Python", "highlight": true}, ...]

WHY SEGMENTS INSTEAD OF HTML (<mark>python</mark>)
--------------------------------------------------
Document text comes from arbitrary web pages. If we returned HTML and the UI
inserted it with innerHTML, a page containing "<script>…" or "<img onerror=…>"
would run code in the user's browser (cross-site scripting). With segments,
the frontend renders plain text and adds <mark> itself. Nothing from a crawled
page is ever interpreted as HTML.
"""

from dataclasses import dataclass

from app.indexing.analyzer import Analyzer

WINDOW = 32          # tokens per snippet window
MAX_CHARS = 320
FALLBACK_CHARS = 240


@dataclass
class Snippet:
    segments: list[dict]
    text: str

    def as_dict(self) -> dict:
        return {"segments": self.segments, "text": self.text}


def _segments(text: str, spans: list[tuple[int, int]]) -> list[dict]:
    out, cursor = [], 0
    for start, end in spans:
        if start > cursor:
            out.append({"text": text[cursor:start], "highlight": False})
        out.append({"text": text[start:end], "highlight": True})
        cursor = end
    if cursor < len(text):
        out.append({"text": text[cursor:], "highlight": False})
    return out


def highlight(text: str, query_terms: set[str], analyzer: Analyzer) -> list[dict]:
    """Highlight every query-term occurrence in a short text (e.g. the title)."""
    spans = [(t.start, t.end) for t in analyzer.analyze(text) if t.term in query_terms]
    return _segments(text, spans)


def make_snippet(text: str, query_terms: set[str], analyzer: Analyzer,
                 window: int = WINDOW, max_chars: int = MAX_CHARS) -> Snippet:
    text = text or ""
    tokens = analyzer.trace(text)  # every token, including stop words, with offsets
    hits = [i for i, t in enumerate(tokens) if t.term is not None and t.term in query_terms]

    if not hits:
        cut = text[:FALLBACK_CHARS]
        if len(text) > FALLBACK_CHARS:
            cut = cut.rsplit(" ", 1)[0] + " …"
        cut = cut.replace("\n", " ")
        return Snippet([{"text": cut, "highlight": False}] if cut else [], cut)

    # --- best window: most distinct terms, then most hits, then earliest
    best = (-1, -1, 0)
    best_start = hits[0]
    for h in hits:
        start = max(0, h - window // 4)  # show a little context before the first hit
        in_window = [i for i in hits if start <= i < start + window]
        distinct = len({tokens[i].term for i in in_window})
        score = (distinct, len(in_window), -start)
        if score > best:
            best, best_start = score, start
    first = best_start
    last = min(len(tokens) - 1, best_start + window - 1)

    char_start, char_end = tokens[first].start, tokens[last].end
    if char_end - char_start > max_chars:  # very long tokens/lines: cap by characters
        char_end = char_start + max_chars
        while last > first and tokens[last].end > char_end:
            last -= 1
        char_end = tokens[last].end

    # if only punctuation/whitespace remains after the window, include it instead of "…"
    if not text[char_end:].strip(" \t\n.,;:!?)\"'"):
        char_end = len(text.rstrip())
    excerpt = text[char_start:char_end]
    spans = [
        (tokens[i].start - char_start, tokens[i].end - char_start)
        for i in hits if first <= i <= last
    ]
    segments = _segments(excerpt, spans)
    if char_start > 0:
        segments.insert(0, {"text": "… ", "highlight": False})
    if char_end < len(text.rstrip()):
        segments.append({"text": " …", "highlight": False})
    for s in segments:  # snippets are shown on one line
        s["text"] = s["text"].replace("\n", " ")
    return Snippet(segments, "".join(s["text"] for s in segments))
