"""
TF-IDF, implemented by hand.

    TF(t, d)      = number of times term t occurs in document d
    IDF(t)        = log( N / df(t) )
    TF-IDF(t, d)  = TF(t, d) x IDF(t)
    score(q, d)   = sum over query terms t of TF-IDF(t, d)

    N      = total number of documents in the collection
    df(t)  = document frequency: how many documents contain t

INTUITION
---------
* TF:  a document that mentions "python" 10 times is probably more about Python
       than one that mentions it once.
* IDF: a term that appears in EVERY document ("program" in a programming
       corpus) tells us nothing about which document to pick, so its weight
       should be ~0. A term in only 2 of 1,000 documents is very informative.
       log(N/df) does exactly that: df = N -> log(1) = 0; small df -> large.

Example with N = 100:
    df = 100  -> IDF = log(1)    = 0.00
    df = 10   -> IDF = log(10)   = 2.30
    df = 1    -> IDF = log(100)  = 4.61

KNOWN WEAKNESSES (why BM25 exists)
----------------------------------
1. TF grows without limit: 50 mentions score 50x one mention, so keyword
   stuffing works and one repeated term can drown out the others.
2. No length normalisation: a long document mentions everything more often
   and wins simply by being long.
"""

import math


def idf(n_docs: int, df: int) -> float:
    """log(N / df). 0 when the term is in every document; undefined (0) when df = 0."""
    if df <= 0 or n_docs <= 0:
        return 0.0
    return math.log(n_docs / df)


def tfidf(tf: int, df: int, n_docs: int) -> float:
    return tf * idf(n_docs, df)
