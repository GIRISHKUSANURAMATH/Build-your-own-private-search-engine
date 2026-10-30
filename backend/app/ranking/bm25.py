"""
BM25 (Okapi "Best Match 25"), implemented by hand.

                                   tf x (k1 + 1)
    score(t, d) = IDF(t) x ------------------------------------------
                            tf + k1 x (1 - b + b x |d| / avgdl)

    IDF(t) = ln( 1 + (N - df + 0.5) / (df + 0.5) )

    tf     term frequency of t in d
    df     number of documents containing t
    N      number of documents
    |d|    length of d (number of index terms)
    avgdl  average document length in the collection
    k1     TF saturation (typical 1.2 - 2.0). Larger = TF matters longer.
    b      length normalisation strength, 0..1 (typical 0.75).
           b = 0: ignore length;  b = 1: fully normalise by length.

WHAT IT FIXES COMPARED TO TF-IDF
--------------------------------
1. TF SATURATION.  tf x (k1+1) / (tf + k1) approaches k1+1 as tf grows:
       tf:     1     2     5     10    50
       k1=1.2: 1.00  1.38  1.77  1.96  2.15   (x IDF)
   The 50th mention adds almost nothing, so keyword stuffing stops paying off
   and documents that match *more different* query terms win.
2. LENGTH NORMALISATION.  A document twice as long as average needs more
   mentions to reach the same score, so long documents can't win just by
   being long.
3. IDF never negative.  The "+1" inside ln() is the Lucene/Elasticsearch
   variant: classic BM25 IDF goes negative for terms in over half the
   documents, which would make matching a common term *lower* a score.
"""

import math

DEFAULT_K1 = 1.2
DEFAULT_B = 0.75


def idf(n_docs: int, df: int) -> float:
    if df <= 0 or n_docs <= 0:
        return 0.0
    return math.log(1 + (n_docs - df + 0.5) / (df + 0.5))


def tf_component(tf: int, doc_len: int, avg_len: float, k1: float = DEFAULT_K1, b: float = DEFAULT_B) -> float:
    if tf <= 0:
        return 0.0
    norm = 1 - b + b * (doc_len / avg_len) if avg_len > 0 else 1.0
    return tf * (k1 + 1) / (tf + k1 * norm)


def bm25(tf: int, df: int, n_docs: int, doc_len: int, avg_len: float,
         k1: float = DEFAULT_K1, b: float = DEFAULT_B) -> float:
    return idf(n_docs, df) * tf_component(tf, doc_len, avg_len, k1, b)
