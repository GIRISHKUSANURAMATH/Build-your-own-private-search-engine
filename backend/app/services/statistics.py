"""Statistics for the admin dashboard (GET /stats, GET /index/stats)."""

from pathlib import Path

from app.database.database import get_connection
from app.indexing.indexer import SearchIndex
from app.services.history_service import HistoryService


def index_stats(index: SearchIndex, top_n: int = 15) -> dict:
    stats = index.stats()
    with index.lock:
        body = index.body
        top = sorted(body.postings.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:top_n]
        # Stems ("mani", "us") mean little to people; show the shortest real word
        # that produces each stem ("many", "use") alongside it.
        wanted = {t for t, _ in top}
        word_for: dict[str, str] = {}
        for word, term in index.vocabulary.items():
            if term in wanted and (term not in word_for or (len(word), word) < (len(word_for[term]), word_for[term])):
                word_for[term] = word
        stats["top_terms"] = [
            {"term": t, "word": word_for.get(t, t), "df": len(p), "cf": sum(x.tf for x in p.values())}
            for t, p in top
        ]
    return stats


def collection_stats(db_path: Path, index: SearchIndex) -> dict:
    with get_connection(db_path) as conn:
        documents = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        domains = conn.execute(
            "SELECT domain, COUNT(*) AS n FROM documents GROUP BY domain ORDER BY n DESC, domain LIMIT 10"
        ).fetchall()
        domain_count = conn.execute("SELECT COUNT(DISTINCT domain) FROM documents").fetchone()[0]
        url_status = dict(conn.execute("SELECT status, COUNT(*) FROM urls GROUP BY status").fetchall())
        jobs = conn.execute(
            """SELECT COUNT(*) AS jobs, COALESCE(SUM(pages_fetched), 0) AS fetched,
                      COALESCE(SUM(pages_failed), 0) AS failed FROM crawl_jobs"""
        ).fetchone()
        postings_bytes = conn.execute(
            "SELECT COALESCE(SUM(length(positions)), 0) + COUNT(*) * 12 FROM postings"
        ).fetchone()[0]
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]

    history = HistoryService(db_path)
    return {
        "documents": documents,
        "indexed_documents": index.doc_count,
        "domains": domain_count,
        "top_domains": [{"domain": r[0], "documents": r[1]} for r in domains],
        "crawl": {
            "jobs": jobs["jobs"],
            "pages_fetched": jobs["fetched"],
            "pages_failed": jobs["failed"],
            "urls_by_status": url_status,
            "duplicates": url_status.get("duplicate", 0),
            "blocked_by_robots": url_status.get("blocked", 0),
        },
        "index": index_stats(index),
        "index_size_bytes": postings_bytes,
        "database_size_bytes": page_size * page_count,
        "search": history.summary(),
        "top_queries": history.popular(10),
    }
