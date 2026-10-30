# Search: indexing, query parsing and retrieval

Code: `backend/app/indexing/` (index), `backend/app/search/` (parser, engine,
fuzzy, snippets, suggestions). Text processing (tokenizer, stemmer...) is in
[text-processing.md](text-processing.md); ranking in [ranking.md](ranking.md).

## 1. The inverted index

Normally we think *document → words*. A search engine flips this around to
*word → documents*:

```
python   → doc 1 (tf 3, positions 4 10 21), doc 7 (tf 1, position 8)
database → doc 2 (tf 2, positions 1 9),     doc 3 (tf 1, position 0)
```

Each entry is a **posting**: (document id, term frequency, positions). The
list of postings for a term is its **postings list**; its length is the
term's **document frequency (df)**.

Finding the documents that contain "python" is now one dictionary lookup, not
a scan of every document. The cost of a query depends on how many documents
contain its terms, not on how big the collection is.

### Two fields

Title and body are indexed separately (`SearchIndex.fields["title"]` and
`["body"]`). That makes `title:word` queries possible and lets ranking
boost title matches.

### The forward index

`ForwardIndex` stores *document → terms with frequencies*. The inverted index
answers queries; the forward index answers questions about one document:
- **Re-indexing or deleting a document:** which postings lists contain it?
  Without the forward index you would have to scan every list.
- **The document view:** the "most frequent terms" panel.

### Storage

| Where | What | Why |
|-------|------|-----|
| Memory | `dict[term, dict[doc_id, Posting]]` per field, forward index, vocabulary | Search must be fast: dictionary lookups, no SQL |
| SQLite `terms` | (field, term) → id, df | Persistent, queryable statistics |
| SQLite `postings` | (term_id, doc_id) → tf, positions as 4-byte unsigned ints (BLOB) | Compact, ordered by term (`WITHOUT ROWID`) |
| SQLite `doc_stats` | doc → body/title length, content hash | BM25 needs lengths; the hash detects changed documents |
| SQLite `vocabulary` | real word → term | Fuzzy matching and autocomplete need words, not stems |

**Consistency rule:** every change is written to SQLite in one transaction
first, and memory is updated only after the commit succeeds. A failed write
therefore never leaves memory and disk disagreeing (`test_failed_write_leaves_memory_untouched`).
On startup, `load()` rebuilds memory from the tables without re-analysing any
text.

## 2. Query syntax

| You type | Meaning |
|----------|---------|
| `python programming` | Any of the words (ranked; more matches score higher). In "All words" mode, all of them |
| `python AND django` | Both (operators must be UPPERCASE) |
| `python OR java` | Either |
| `python NOT java`, `python -java` | Exclude |
| `"machine learning"` | Exact phrase |
| `(python OR java) AND web` | Grouping |
| `title:python`, `title:"neural networks"` | Term or phrase in the title |
| `site:example.com` | Only that domain and its subdomains |
| `author:asha` | Author name contains these words |
| `machne~` | Typo-tolerant for this word |

**Modes** (the buttons under the search box): *Any words* = default OR,
*All words* = default AND, *Exact phrase* = the whole query is one phrase,
*Typo tolerant* = every word gets `~`.

## 3. The query parser (recursive descent)

```
query    := or_expr
or_expr  := seq ( "OR" seq )*
seq      := and_expr+                 (joined with the default operator)
and_expr := unary ( "AND" unary )*
unary    := ("NOT" | "-") unary | atom
atom     := "(" or_expr ")" | "phrase" | field:value | word[~]
```

There is one Python method per grammar rule, each calling the rules below it.
Precedence comes from that nesting: AND binds tighter than OR, so
`a OR b AND c` = `a OR (b AND c)`.

Two rules apply to the whole sequence of words, whatever the default operator:
- `NOT x` excludes x from the result of the sequence.
- `site:` and `author:` filters are always ANDed (a filter that was ORed in
  would filter nothing).

The parser **never raises an error on user input**. An unclosed quote or
bracket is closed for the user, and the response carries a warning.
`GET /search` returns the parsed tree, e.g. `AND(OR(python, java), NOT(c++))`,
and the UI shows it under "How PySearch read your query".

## 4. Boolean retrieval

The query tree is evaluated into a set of document ids:

| Node | Operation on postings |
|------|-----------------------|
| `Term` | `docs(term)` in body ∪ title (or title only for `title:`) |
| `AND` | intersection (smallest set first: the result can't be bigger than it) |
| `OR` | union |
| `NOT` | difference |
| `Phrase` | intersection, then a position check (below) |
| `site:` / `author:` | filter on per-document metadata kept in memory |

A word that is only a stop word ("the") becomes "no constraint" rather than
"matches nothing", so `the python` behaves like `python`, with a warning.

## 5. Phrase search with positions

`"machine learning"` → analyzer → `machin@0, learn@1`. A document matches if,
for some position *p*, `machin` occurs at *p* **and** `learn` at *p + 1*.

Stop-word gaps are kept. `"university of london"` → `univers@0, london@2`, so
London must be exactly two positions after University, as in the original
text. `"university london"` (no gap) does not match that document.

## 6. Fuzzy matching

`find_similar()` compares the word with every vocabulary word using
**Damerau–Levenshtein distance**: insertions, deletions, substitutions and
transpositions each cost 1. The distance is computed with dynamic programming
in O(len(a) × len(b)), and the computation stops early once a whole row
exceeds the limit.

The allowed distance depends on word length: 0 for up to 3 letters, 1 for
4–6, 2 for 7 or more. Short words have too many close neighbours to allow
typos ("cat" is one edit from bat, car, cut...).

Candidates are ranked by (distance, popularity = df). Fuzzy expansions are
scored at 0.8× the weight of exact matches.

**"Did you mean"**: when a word matches nothing and the query returns fewer
than 5 results, the closest popular vocabulary word is suggested:
`machne lerning` → **machine learning**.

## 7. Snippets and highlighting

1. Re-analyze the document text. Tokens come back with their term and exact
   character offsets.
2. Slide a 32-token window and pick the one with the most different query
   terms, then the most matches, then the earliest.
3. Cut the original text at the window's offsets and add "…" where text was
   removed.
4. Return **segments**: `[{"text": "Learn ", "highlight": false},
   {"text": "Python", "highlight": true}, ...]`.

Segments instead of HTML is a security decision. Crawled pages can contain
`<script>` or `<img onerror=...>`. The frontend renders segments as plain
text and adds `<mark>` itself, so nothing from a page is ever interpreted as
HTML.

## 8. Autocomplete

`GET /search/suggest?q=mach` combines:
1. **Previous searches** starting with the typed text that returned results,
   most frequent first.
2. **Completions of the last word** from the vocabulary, ranked by df. The
   vocabulary is kept as a sorted list, so `bisect` finds the block of words
   with a given prefix in O(log n).

No AI or external service is involved.

## 9. Search history and analytics

Every first-page search is stored (query, mode, ranker, result count,
latency); clicking a result records which document was chosen. The admin page
shows the most frequent queries, zero-result searches and average latency.
`DELETE /search/history` clears it.

---

## Viva questions

**Q: Why use an inverted index?**
Looking up a term gives its documents directly. Without it, every query would
read every document, so the cost would grow with the size of the collection.

**Q: What is a postings list?**
The list of (document, term frequency, positions) entries for one term. Its
length is the term's document frequency.

**Q: Why keep a forward index as well?**
To remove or re-index a document without scanning every postings list, and to
show a document's own term statistics.

**Q: How does Boolean search work?**
Each term gives a set of documents; AND is intersection, OR union, NOT
difference. Intersecting the smallest set first keeps it fast.

**Q: How does phrase search work?**
Intersect the documents of all the phrase's terms, then check positions:
term *k* must sit at the first term's position + offset *k*. Offsets include
the gaps where stop words were removed.

**Q: How does fuzzy search work?**
Edit distance (insert, delete, substitute, transpose) between the query word
and the vocabulary, with dynamic programming. The allowed distance grows with
word length, and candidates are ranked by distance, then popularity.

**Q: Why return snippets as segments instead of HTML?**
To prevent cross-site scripting: page text is never inserted as HTML.

**Q: Why does fuzzy matching use the vocabulary instead of the index terms?**
Index terms are stems ("machin"). Users type and read real words, so
suggestions and edit distances must use real words.
