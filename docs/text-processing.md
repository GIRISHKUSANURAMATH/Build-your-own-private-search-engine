# Text Processing (Phase 3)

How PySearch turns a web page into the terms that go into the index, and why
each step exists. Code lives in `backend/app/indexing/`.

```
 HTML page
    │  html_extractor.py   parse, drop scripts/menus/footers, pick main content, read metadata
    ▼
 raw text
    │  cleaner.py          fix whitespace, remove invisible characters (keeps case/punctuation)
    ▼
 clean text  ───────────────────────────────►  stored in documents.content, shown in snippets
    │  tokenizer.py        split into words, remember character offsets
    ▼
 tokens
    │  normalizer.py       casefold, strip Latin accents, NFKC, apostrophes, 1,000 → 1000
    ▼
 normalized tokens ─────────────────────────►  kept for fuzzy matching (real words)
    │  stopwords.py        drop "the", "is", "of"... (positions are NOT renumbered)
    ▼
 content tokens
    │  stemmer.py          Porter: programming → program, languages → languag
    ▼
 index terms  ──────────────────────────────►  Phase 4 inverted index
```

`analyzer.py` wires the last four steps together. **The same Analyzer must
process documents and queries**; that is the single most important rule in
this file.

Worked example, `"Python is a powerful programming language."`:

| original    | normalized  | dropped  | term     | position |
|-------------|-------------|----------|----------|----------|
| Python      | python      |          | python   | 0 |
| is          | is          | stopword |          | 1 |
| a           | a           | stopword |          | 2 |
| powerful    | powerful    |          | power    | 3 |
| programming | programming |          | program  | 4 |
| language    | language    |          | languag  | 5 |

Try it yourself: start the backend, open http://127.0.0.1:8000/docs, and call
`POST /analyze`.

---

## 1. HTML extraction

**Problem.** A typical page is mostly not content: scripts, CSS, menus, cookie
banners, footers. Index all of it and every page on a site matches "Home",
"Privacy" and "Login", while the actual article is diluted.

**What we do** (`extract_html`):

1. Parse with BeautifulSoup using Python's built-in `html.parser`. This is a
   *tree builder*: it never runs JavaScript and never fetches anything.
2. Read metadata first: `<title>` (fallbacks: `og:title`, first `<h1>`),
   `meta description`, `meta author`, `<html lang>`, `<link rel="canonical">`.
3. Delete elements that are never content: `script`, `style`, `svg`, `iframe`,
   form controls, and anything hidden (`hidden`, `aria-hidden="true"`,
   `display:none`).
4. Delete page chrome: `nav`, `aside`, page-level `header`/`footer` (a header
   *inside* an `<article>` is kept, because it usually holds the article
   title), ARIA landmarks (`role="navigation"`, `banner`, `contentinfo`...),
   and elements whose class or id is exactly `sidebar`, `breadcrumb`,
   `cookie-banner`, etc. The match is exact on purpose: substring matching on
   "comment" would delete real content.
5. Choose the main content root: `<main>` or `role="main"`, then the largest
   `<article>`, then `<body>`. If the root has under 80 characters of text,
   fall back to `<body>`.
6. Walk the tree and put a newline at every block element (`p`, `li`, `h1`,
   `br`, `tr`...), so paragraphs survive into the stored text.

**Limits.** This is a heuristic. Pages that render their content with
JavaScript (single-page apps) have little HTML text to extract. Input over
5 MB is truncated before parsing (`truncated=True`).

Plain-text files go through `extract_text` (clean only, first line becomes
the title). Other content types raise `UnsupportedContentType`.

## 2. Cleaning

Cleaning makes text tidy **without changing its words**. Case and punctuation
stay, because this is the text users read in result snippets.

* `\r\n` → `\n`
* remove invisible characters: zero-width space, BOM, soft hyphen, control
  characters. `"pyth​on"` would otherwise tokenize as two words.
* every kind of space (non-breaking, tab...) → one ordinary space
* at most one blank line between paragraphs

## 3. Tokenization

**Token** = one unit of text the engine treats as a word. Tokenizing decides
what can be searched: if "C++" became "C", nobody could search for C++.

| Input | Tokens |
|-------|--------|
| `state-of-the-art` | state, of, the, art |
| `it's don't` | it's, don't (apostrophe between letters kept) |
| `3.14  1,000  192.168.0.1  python3.12` | each stays one token |
| `C++  C#  F#` | c++, c#, f# |
| `ಕನ್ನಡ ಭಾಷೆ` | ಕನ್ನಡ, ಭಾಷೆ |

**Why not `re.findall(r"\w+", text)`?** Python's `\w` doesn't match Unicode
*combining marks*. In Kannada, Hindi and Tamil, vowel signs and the virama are
combining marks, so `\w+` cuts words apart:

```python
>>> re.findall(r"\w+", "ಕನ್ನಡ")
['ಕನ', 'ನಡ']
```

Our tokenizer scans character by character using Unicode categories:
letters (L) and numbers (N) start or continue a word, and combining marks (M)
continue one.

Each token keeps `start`/`end` offsets, so `text[start:end]` is exactly the
token. Snippet highlighting (Phase 5) uses these offsets.

## 4. Normalization

The index is an exact-match dictionary, so `Python` and `python` would be
different keys. Normalization maps variants to one form:

| Step | Example |
|------|---------|
| Unicode NFKC | `ﬁle` (one ligature character) → `file`, `Ｐｙｔｈｏｎ` → `Python` |
| `casefold()` (stronger than `lower()`) | `Straße` → `strasse` |
| Strip accents from Latin letters | `café` → `cafe` |
| Possessive / apostrophes | `Python's` → `python`, `don't` → `dont` |
| Thousands separators | `1,000,000` → `1000000` |

**Accent folding only touches Latin letters.** Unicode stores `é` as `e` plus
a combining accent, and removing combining marks removes the accent. But in
Indic scripts the same kind of character is a vowel sign. Strip it from
`ಕನ್ನಡ` and you get a different, meaningless string. So we drop a mark only
when it sits on a Latin base letter.

## 5. Stop words

Stop words are very common words ("the", "is", "of") that appear in almost
every document. They can't distinguish one document from another, and they
make up the largest postings lists in the index.

Trade-offs:
* Queries made only of stop words return nothing: "to be or not to be", "The Who".
* Phase 6 will show IDF automatically giving common words a weight near zero,
  which is why many modern engines keep stop words. Removal is a switch on
  the Analyzer (`remove_stopwords=False`).

**Positions keep gaps.** `"university of london"` becomes `univers@0,
london@2`, not `london@1`. Phrase search can then require exactly the
spacing the phrase had.

## 6. Stemming (Porter, 1980)

**Stemming** cuts suffixes so that word forms meet:
`connect, connected, connecting, connection, connections → connect`.

A stem need not be a real word (`happy → happi`, `language → languag`). That's
fine, because stems are only ever compared with other stems.

### How Porter works

Any word has the form `[C](VC)^m[V]`: C is a run of consonants, V a run of
vowels, and **m (the "measure")** is roughly the number of syllables.

| m | examples |
|---|----------|
| 0 | tr, ee, tree, by |
| 1 | trouble, oats, trees, ivy |
| 2 | troubles, private, oaten |

Rules have the form `(condition) SUFFIX → REPLACEMENT`, for example
`(m>0) ational → ate`. The conditions protect short words: `relational →
relate`, because the stem `rel` has m=1. But `rational` keeps its ending in
step 2, because `r` has m=0.

Five steps run in order:

1. Plurals and -ed/-ing: `caresses → caress`, `motoring → motor`, `hopping → hop`
2. Double suffixes: `relational → relate`, `digitizer → digitize`
3. `-ic-`, `-ful`, `-ness`: `hopeful → hope`, `goodness → good`
4. Remove `-ant`, `-ence`, `-ment`...: `adjustment → adjust` (needs m>1)
5. Tidy up: `probate → probat`, `controll → control`

If the longest matching suffix fails its condition, no shorter suffix is
tried. `_apply_longest()` implements exactly that.

### Weaknesses

* **Over-stemming:** `university`, `universe`, `universal` → `univers`
  (unrelated words merge, which hurts precision)
* **Under-stemming:** `alumnus`/`alumni` stay different (related words don't
  meet, which hurts recall)
* English only. Words in other scripts, numbers and `c++` pass through
  unchanged.

### Stemming vs lemmatization

**Lemmatization** maps words to their dictionary form using a vocabulary and
part of speech (`better → good`, `ran → run`, `mice → mouse`). It's more
accurate but needs a large dictionary (e.g. WordNet) and a part-of-speech
tagger. PySearch uses stemming: no dependencies, fast, and fully explainable.
For ranking, stems work about as well.

### How we know our stemmer is correct

`tests/data/porter_vocabulary.tsv` holds 4,082 word → stem pairs produced by
NLTK's paper-exact Porter implementation. Across a 60,013-word vocabulary,
the two implementations disagreed on zero words.

One intentional difference: words of one or two letters are returned
unchanged, as in Porter's own reference implementation. NLTK's paper-exact
mode would turn `"as"` into `"a"`.

---

## Viva questions

**Q: What is tokenization and why does it matter?**
Splitting text into the units that get indexed. It decides what can be
searched: a tokenizer that turns "C++" into "c" makes C++ unsearchable.

**Q: Why normalize text?**
The index matches strings exactly. Normalization maps case, accent and
Unicode variants to one form, so a query for "cafe" finds "Café".

**Q: Why must documents and queries use the same analyzer?**
The index stores processed terms (`program`). A query that isn't processed
the same way looks up `programming` and finds nothing.

**Q: Why remove stop words? What is the downside?**
They appear everywhere, can't tell documents apart, and make up the largest
postings lists. The downside: queries made of stop words ("The Who") become
impossible. Removal is therefore configurable, and IDF can do the job instead.

**Q: Stemming vs lemmatization?**
Stemming chops suffixes by rule (fast, no dictionary, sometimes produces
non-words, can over-merge). Lemmatization looks words up to get the
dictionary form (accurate, but needs a vocabulary and a part-of-speech
tagger).

**Q: What is the "measure" in Porter's algorithm?**
m in `[C](VC)^m[V]`, roughly the syllable count of the stem. Rule conditions
like `m>0` stop the algorithm from stripping suffixes off very short words.

**Q: Why are positions not renumbered after stop-word removal?**
So phrase search can check that the words of a phrase are the same distance
apart in the document as in the query.

**Q: Why not just use `\w+` to tokenize?**
Python's `\w` doesn't count combining marks as word characters, which splits
words in Indic scripts. We scan by Unicode category instead.

**Q: Why store the original text separately from the index terms?**
Terms are lossy (lower-cased, stemmed). Snippets need the original text, and
fuzzy matching needs real words, not stems.
