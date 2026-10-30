# Ranking: TF-IDF and BM25

Code: `backend/app/ranking/tfidf.py`, `bm25.py`, `ranker.py`. Both are
implemented by hand; no library computes the scores.

Boolean retrieval decides **which** documents match. Ranking decides the
**order**. Switch with `?ranker=tfidf|bm25` or the *Ranking* menu in the UI.
Tick "Show score details" to see every term's contribution.

## TF-IDF

```
TF(t, d)     = number of times term t occurs in document d
IDF(t)       = log( N / df(t) )
TF-IDF(t, d) = TF(t, d) × IDF(t)
score(q, d)  = Σ over query terms t of TF-IDF(t, d)
```

| Symbol | Meaning |
|--------|---------|
| N | number of documents in the collection |
| df(t) | number of documents containing t |
| tf | occurrences of t in d |

**Why IDF reduces the importance of common words:** if a word is in every
document, df = N, so IDF = log(1) = 0. It can't help choose between documents,
so it contributes nothing. A rare word (small df) gets a large IDF. With
N = 100: df 100 → 0.00, df 10 → 2.30, df 1 → 4.61 (natural log).

**Weaknesses**
1. TF has no ceiling. Fifty mentions score fifty times one mention, so
   keyword stuffing pays off.
2. No length normalisation. Long documents mention everything more often and
   win by being long.

## BM25

```
                                tf × (k1 + 1)
score(t, d) = IDF(t) × ------------------------------------
                        tf + k1 × (1 − b + b × |d| / avgdl)

IDF(t) = ln( 1 + (N − df + 0.5) / (df + 0.5) )
```

| Parameter | Default | Meaning |
|-----------|---------|---------|
| k1 | 1.2 | How quickly TF saturates. Higher = repeated words keep counting longer |
| b | 0.75 | Length normalisation. 0 = ignore length, 1 = fully normalise |
| \|d\| | | length of the document (number of index terms) |
| avgdl | | average document length in the collection |

**1. TF saturation.** The TF part approaches k1 + 1 = 2.2 however large tf
gets:

| tf | 1 | 2 | 5 | 10 | 50 |
|----|---|---|---|----|----|
| TF part (k1 = 1.2, average length) | 1.00 | 1.38 | 1.77 | 1.96 | 2.15 |

**2. Length normalisation.** A document twice the average length needs more
occurrences to reach the same score.

**3. Non-negative IDF.** The "+1" inside ln() (the Lucene/Elasticsearch
variant) stops very common terms from getting a negative IDF.

## Fields and title boost

Each query term is scored in the body and in the title, and the title score
is multiplied by **2.0** (`TITLE_BOOST`). `title:word` terms are scored in
the title only. This is a simplified version of BM25F, the field-aware form
of BM25.

## Does BM25 beat TF-IDF here?

`python manage.py evaluate` runs the 20 hand-labelled queries in
`data/eval/queries.json` against the 44-document sample collection:

| Metric | TF-IDF | BM25 |
|--------|--------|------|
| P@5 | 0.250 | 0.260 |
| R@5 | 0.975 | 1.000 |
| F1@5 | 0.390 | 0.405 |
| MAP | 0.943 | 0.979 |
| MRR | 0.975 | 1.000 |
| nDCG@10 | 0.966 | 0.990 |

The sample includes one long glossary page that mentions almost every topic
several times. **TF-IDF ranks it second for 8 of the 20 queries; BM25 pushes
it down**, because TF saturation and length normalisation stop it winning on
repetition and length alone. For "python async", TF-IDF even puts "Python
Basics" above the asyncio article; BM25 gets it right.

**Be honest about this result in a viva.** 20 queries, 44 documents, and the
same person wrote both the documents and the relevance labels. That
demonstrates the effect clearly but is not proof. Precision@5 is low for both
rankers simply because most queries have only one or two relevant documents.

### Metrics used

| Metric | Formula | Question it answers |
|--------|---------|---------------------|
| Precision@k | relevant in top k / k | Of what I showed, how much was useful? |
| Recall@k | relevant in top k / all relevant | Of everything useful, how much did I show? |
| F1@k | 2PR / (P + R) | Both at once (harmonic mean) |
| AP / MAP | mean of Precision@i at each relevant position | Are relevant documents ranked high? |
| MRR | 1 / rank of the first relevant result | How far down is the first good result? |
| nDCG@k | Σ 1/log2(rank+1) for relevant, divided by the ideal | Graded by position; 1.0 = perfect |

**Why precision, recall and F1 alone can't compare the two rankers:** both
retrieve exactly the same set of matching documents and differ only in order.
Set-based precision and recall over all results would be identical. Only the
rank-aware metrics (and @k cut-offs) show the difference.

---

## Viva questions

**Q: What is TF-IDF?**
A term's weight in a document is how often it appears there (TF) times how
rare it is in the collection (IDF = log N/df). The document's score is the sum
over the query terms.

**Q: Why does IDF reduce the importance of common words?**
For a word in every document, N/df = 1 and log 1 = 0. The rarer the word, the
larger its IDF.

**Q: Why use BM25 instead of TF-IDF?**
Term frequency saturates (repetition stops paying off) and scores are
normalised by document length (long documents don't win just by being long).
Both are tunable with k1 and b.

**Q: What do k1 and b do?**
k1 sets how quickly extra occurrences stop adding score; b sets how strongly
document length is penalised (0 = not at all, 1 = fully).

**Q: How did you show BM25 is better?**
Hand-labelled queries, rank-aware metrics (MAP, MRR, nDCG), both rankers run
on identical candidates. BM25 scored higher, mainly by demoting a long,
repetitive page. The test set is small and self-labelled, so it's a
demonstration, not proof.
