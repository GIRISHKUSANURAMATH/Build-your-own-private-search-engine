"""
Compare TF-IDF and BM25 on the hand-labelled queries (data/eval/queries.json),
and benchmark indexing and search speed.

Runs on a TEMPORARY database loaded with the sample collection, so your real
data never affects (or is affected by) the evaluation.
"""

import json
import statistics
import tempfile
import time
import tracemalloc
from pathlib import Path

from app.database.database import init_db
from app.evaluation.metrics import average_precision, f1, ndcg_at_k, precision_at_k, recall_at_k, reciprocal_rank
from app.indexing.indexer import SearchIndex
from app.search.search_engine import SearchEngine
from app.services.index_service import IndexService
from app.services.sample_data import EVAL_FILE, SAMPLE_FILE, load_sample

K = 5


def build_sample_engine(db_dir: Path) -> tuple[SearchEngine, dict[str, int], dict]:
    db = db_dir / "eval.db"
    init_db(db)
    index = SearchIndex()
    index.load(db)
    tracemalloc.start()
    t0 = time.perf_counter()
    loaded = load_sample(IndexService(db, index), SAMPLE_FILE)
    index_seconds = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    bench = {"documents": index.doc_count, "terms": index.body.term_count,
             "postings": index.body.posting_count + index.title.posting_count,
             "indexing_seconds": round(index_seconds, 3), "peak_memory_mb": round(peak / 1e6, 2)}
    return SearchEngine(index, db), loaded["slugs"], bench


def evaluate(queries_file: Path = EVAL_FILE, k: int = K) -> dict:
    queries = json.loads(queries_file.read_text(encoding="utf-8"))["queries"]
    with tempfile.TemporaryDirectory() as tmp:
        engine, slugs, bench = build_sample_engine(Path(tmp))
        id_to_slug = {v: k_ for k_, v in slugs.items()}
        report: dict = {"k": k, "queries": [], "rankers": {}, "benchmark": bench}
        latencies: dict[str, list[float]] = {"bm25": [], "tfidf": []}
        per_ranker: dict[str, list[dict]] = {"bm25": [], "tfidf": []}

        for q in queries:
            relevant = set(q["relevant"])
            row = {"query": q["query"], "relevant": q["relevant"]}
            for ranker in ("bm25", "tfidf"):
                t0 = time.perf_counter()
                res = engine.search(q["query"], ranker=ranker, page_size=50)
                latencies[ranker].append((time.perf_counter() - t0) * 1000)
                ranked = [id_to_slug[r["id"]] for r in res["results"]]
                p, r = precision_at_k(ranked, relevant, k), recall_at_k(ranked, relevant, k)
                m = {"top": ranked[:k], "precision": p, "recall": r, "f1": f1(p, r),
                     "ap": average_precision(ranked, relevant), "rr": reciprocal_rank(ranked, relevant),
                     "ndcg": ndcg_at_k(ranked, relevant, 10), "retrieved": res["total"]}
                per_ranker[ranker].append(m)
                row[ranker] = m
            report["queries"].append(row)

        for ranker, rows in per_ranker.items():
            report["rankers"][ranker] = {
                f"P@{k}": round(statistics.mean(x["precision"] for x in rows), 3),
                f"R@{k}": round(statistics.mean(x["recall"] for x in rows), 3),
                f"F1@{k}": round(statistics.mean(x["f1"] for x in rows), 3),
                "MAP": round(statistics.mean(x["ap"] for x in rows), 3),
                "MRR": round(statistics.mean(x["rr"] for x in rows), 3),
                "nDCG@10": round(statistics.mean(x["ndcg"] for x in rows), 3),
            }
            lat = sorted(latencies[ranker])
            report["benchmark"][f"{ranker}_latency_ms_median"] = round(statistics.median(lat), 2)
            report["benchmark"][f"{ranker}_latency_ms_p95"] = round(lat[int(0.95 * (len(lat) - 1))], 2)
        report["benchmark"]["queries"] = len(queries)
    return report


def format_report(report: dict) -> str:
    k = report["k"]
    lines = [f"Evaluation on {report['benchmark']['queries']} labelled queries "
             f"({report['benchmark']['documents']} documents)", ""]
    names = list(report["rankers"]["bm25"])
    lines.append(f"{'metric':<10}{'TF-IDF':>10}{'BM25':>10}")
    for n in names:
        lines.append(f"{n:<10}{report['rankers']['tfidf'][n]:>10.3f}{report['rankers']['bm25'][n]:>10.3f}")
    lines += ["", f"Per query (first relevant result's rank; top {k} shown for misses):"]
    for row in report["queries"]:
        def rank(m):
            return f"#{round(1 / m['rr'])}" if m["rr"] else "miss"
        lines.append(f"  {row['query']:<34} TF-IDF {rank(row['tfidf']):>5}   BM25 {rank(row['bm25']):>5}")
    b = report["benchmark"]
    lines += ["", "Benchmark:",
              f"  indexed {b['documents']} documents ({b['terms']} terms, {b['postings']} postings) "
              f"in {b['indexing_seconds']} s, peak memory {b['peak_memory_mb']} MB",
              f"  search latency median: BM25 {b['bm25_latency_ms_median']} ms, TF-IDF {b['tfidf_latency_ms_median']} ms "
              f"(p95 {b['bm25_latency_ms_p95']} / {b['tfidf_latency_ms_p95']} ms)"]
    return "\n".join(lines)
