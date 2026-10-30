"""
Information-retrieval evaluation metrics.

Given a RANKED list of results and the set of documents a person judged
RELEVANT for the query:

    Precision@k = relevant results in the top k / k
                  "Of what I showed, how much was useful?"
    Recall@k    = relevant results in the top k / all relevant documents
                  "Of everything useful, how much did I show?"
    F1@k        = 2 x P x R / (P + R)      harmonic mean: high only if BOTH are high

WHY RANK-AWARE METRICS TOO
--------------------------
TF-IDF and BM25 retrieve the SAME set of matching documents and differ only
in ORDER. Precision/recall over the whole result set would be identical for
both, so they cannot tell the rankers apart. These metrics care about order:

    Average Precision (AP): average of Precision@i at every position i where a
        relevant document appears. Relevant documents ranked higher -> higher AP.
        MAP = mean of AP over all queries.
    Reciprocal Rank (RR): 1 / position of the first relevant result. MRR = mean.
    nDCG@k: each relevant result gains 1 / log2(position + 1) — a result at
        position 1 counts fully, at position 10 much less — divided by the best
        possible value (all relevant documents at the top), so 1.0 = perfect.
"""

import math


def precision_at_k(ranked: list, relevant: set, k: int) -> float:
    if k <= 0:
        return 0.0
    return sum(1 for d in ranked[:k] if d in relevant) / k


def recall_at_k(ranked: list, relevant: set, k: int) -> float:
    if not relevant:
        return 0.0
    return sum(1 for d in ranked[:k] if d in relevant) / len(relevant)


def f1(p: float, r: float) -> float:
    return 0.0 if p + r == 0 else 2 * p * r / (p + r)


def average_precision(ranked: list, relevant: set) -> float:
    if not relevant:
        return 0.0
    hits, total = 0, 0.0
    for i, d in enumerate(ranked, 1):
        if d in relevant:
            hits += 1
            total += hits / i
    return total / len(relevant)


def reciprocal_rank(ranked: list, relevant: set) -> float:
    for i, d in enumerate(ranked, 1):
        if d in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at_k(ranked: list, relevant: set, k: int) -> float:
    dcg = sum(1.0 / math.log2(i + 1) for i, d in enumerate(ranked[:k], 1) if d in relevant)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(relevant), k) + 1))
    return dcg / ideal if ideal else 0.0
