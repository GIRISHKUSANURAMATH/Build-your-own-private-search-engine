"""
English stop words: very common words that carry little meaning on their own.

WHY REMOVE THEM
---------------
"the" appears in nearly every English document. As a search term it matches
everything, so it cannot help tell documents apart — and its postings list
would be the longest in the index (slow to process, big on disk). Dropping
stop words typically shrinks an English index by 25–40%.

THE COST (trade-offs to mention in a viva)
------------------------------------------
* Some queries are made almost entirely of stop words: "to be or not to be",
  "The Who", "let it be". After removal there is nothing left to search for.
* "us" (the pronoun) and "US" (the country) normalize to the same token.
* Modern engines often KEEP stop words and let IDF down-weight them instead
  (Phase 6 will show IDF doing exactly that). Removal is configurable on the
  Analyzer for that reason.

Positions are preserved even when a stop word is removed (see analyzer.py),
so phrase search still knows that "university" and "london" in
"university of london" are two positions apart.

This list is our own, deliberately moderate (~150 words): function words,
pronouns, auxiliary verbs, and apostrophe-less contractions (the normalizer
turns "don't" into "dont"). Content-bearing words like "computer" or "data"
are never stop words, even though they are common in a CS corpus — IDF
handles corpus-specific frequency.
"""

STOPWORDS: frozenset[str] = frozenset(
    """
    a about above after again against all am an and any are as at
    be because been before being below between both but by
    can cannot could
    did do does doing down during
    each
    few for from further
    had has have having he her here hers herself him himself his how
    i if in into is it its itself
    just
    me more most my myself
    no nor not now
    of off on once only or other our ours ourselves out over own
    same she should so some such
    than that the their theirs them themselves then there these they this those through to too
    under until up
    very
    was we were what when where which while who whom why will with would
    you your yours yourself yourselves
    also may might must shall upon via yet
    im ive id ill youre youve youd youll hes shes its were theyre theyve theyd theyll
    isnt arent wasnt werent hasnt havent hadnt doesnt dont didnt wont wouldnt shant shouldnt
    cant couldnt mustnt lets thats whats wheres whos hows theres heres
    """.split()
)


def is_stopword(normalized_token: str) -> bool:
    """Expects an already-normalized (lower-cased, apostrophe-free) token."""
    return normalized_token in STOPWORDS
