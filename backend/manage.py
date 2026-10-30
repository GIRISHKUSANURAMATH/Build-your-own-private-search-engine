"""
PySearch command-line tool. Run from the backend/ folder with the venv active:

    python manage.py load-sample            # add the 44-document demo collection and index it
    python manage.py crawl --url https://example.com --depth 2 --max-pages 30
    python manage.py index                  # index new/changed documents
    python manage.py rebuild                # rebuild the whole index from the documents table
    python manage.py search "machine learning" --ranker bm25 --explain
    python manage.py stats
    python manage.py evaluate               # TF-IDF vs BM25 on the labelled queries + benchmark
    python manage.py clear-history

Uses the same database as the server (DATABASE_PATH in backend/.env).
The server keeps its own copy of the index in memory: after changing data
here while the server is running, click "Rebuild index" in the admin page
or restart the server.
"""

import argparse
import json
import sys

from app.config import get_settings
from app.crawler.jobs import CrawlManager
from app.database.database import init_db
from app.indexing.indexer import SearchIndex
from app.search.search_engine import SearchEngine
from app.services.history_service import HistoryService
from app.services.index_service import IndexService
from app.services.sample_data import load_sample
from app.services.statistics import collection_stats


def _open():
    settings = get_settings()
    init_db(settings.DATABASE_PATH)
    index = SearchIndex()
    index.load(settings.DATABASE_PATH)
    return settings, index


def cmd_load_sample(args) -> None:
    settings, index = _open()
    result = load_sample(IndexService(settings.DATABASE_PATH, index))
    print(f"Sample collection loaded: {result['created']} created, {result['updated']} updated. "
          f"Index now has {index.doc_count} documents, {index.body.term_count} terms.")


def cmd_index(args) -> None:
    settings, index = _open()
    r = index.index_pending(settings.DATABASE_PATH)
    print(f"Indexed {r['indexed']} new/changed documents in {r['seconds']} s. Total: {index.doc_count}.")


def cmd_rebuild(args) -> None:
    settings, index = _open()
    r = index.rebuild(settings.DATABASE_PATH)
    print(f"Rebuilt index from {r['indexed']} documents in {r['seconds']} s ({index.body.term_count} terms).")


def cmd_crawl(args) -> None:
    settings, index = _open()
    manager = CrawlManager(settings.DATABASE_PATH, IndexService(settings.DATABASE_PATH, index), settings)
    depth = min(args.depth if args.depth is not None else settings.MAX_DEPTH, 5)
    pages = args.max_pages if args.max_pages is not None else settings.MAX_PAGES
    domains = sorted(set(args.allow_domain or []) | set(settings.allowed_domains_list))
    print(f"Crawling {args.url} (depth {depth}, max {pages} pages, delay {settings.REQUEST_DELAY}s) ...")
    job = manager.start(args.url, depth, pages, domains, background=False)
    job = manager.crawl.get_job(job.id)
    print(f"Job {job.id}: {job.status}. discovered {job.pages_discovered}, fetched {job.pages_fetched}, "
          f"failed {job.pages_failed}." + (f" Error: {job.error}" if job.error else ""))
    print(f"Index now has {index.doc_count} documents.")


def cmd_search(args) -> None:
    settings, index = _open()
    res = SearchEngine(index, settings.DATABASE_PATH).search(
        args.query, mode=args.mode, ranker=args.ranker, page_size=args.limit, explain=args.explain)
    print(f"{res['total']} results for {res['parsed']} ({res['ranker']}, {res['took_ms']} ms)")
    if res["did_you_mean"]:
        print(f"Did you mean: {res['did_you_mean']}")
    for w in res["warnings"]:
        print(f"Note: {w}")
    for r in res["results"]:
        print(f"\n{r['rank']:>2}. {r['title']}  [{r['score']}]\n    {r['url']}\n    {r['snippet']['text']}")
        if args.explain:
            for e in r["explain"]:
                print(f"      {e['term']:<12} {e['field']:<5} score {e['score']:<8} tf {e['tf']} df {e['df']} idf {e['idf']}")


def cmd_stats(args) -> None:
    settings, index = _open()
    print(json.dumps(collection_stats(settings.DATABASE_PATH, index), indent=2))


def cmd_evaluate(args) -> None:
    from app.evaluation.evaluate import evaluate, format_report
    report = evaluate(k=args.k)
    print(json.dumps(report, indent=2) if args.json else format_report(report))


def cmd_clear_history(args) -> None:
    settings = get_settings()
    init_db(settings.DATABASE_PATH)
    print(f"Deleted {HistoryService(settings.DATABASE_PATH).clear()} search history entries.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="manage.py", description="PySearch command-line tool")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("load-sample", help="load the demo collection").set_defaults(fn=cmd_load_sample)
    sub.add_parser("index", help="index new/changed documents").set_defaults(fn=cmd_index)
    sub.add_parser("rebuild", help="rebuild the whole index").set_defaults(fn=cmd_rebuild)
    p = sub.add_parser("crawl", help="crawl a website (polite, restricted)")
    p.add_argument("--url", required=True)
    p.add_argument("--depth", type=int)
    p.add_argument("--max-pages", type=int)
    p.add_argument("--allow-domain", action="append", help="extra domain to follow (repeatable)")
    p.set_defaults(fn=cmd_crawl)
    p = sub.add_parser("search", help="search from the terminal")
    p.add_argument("query")
    p.add_argument("--mode", default="basic", choices=["basic", "boolean", "phrase", "fuzzy"])
    p.add_argument("--ranker", default="bm25", choices=["bm25", "tfidf"])
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--explain", action="store_true")
    p.set_defaults(fn=cmd_search)
    sub.add_parser("stats", help="collection and index statistics").set_defaults(fn=cmd_stats)
    p = sub.add_parser("evaluate", help="compare TF-IDF and BM25 on labelled queries")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_evaluate)
    sub.add_parser("clear-history", help="delete search history").set_defaults(fn=cmd_clear_history)
    args = parser.parse_args(argv)
    args.fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
