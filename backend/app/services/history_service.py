"""Search history: record queries, clicks, recent and popular searches."""

from pathlib import Path

from app.database.database import get_connection
from app.database.repositories import NotFoundError


class HistoryService:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def record(self, query: str, mode: str, ranker: str, results_count: int, latency_ms: float) -> int:
        with get_connection(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO search_history (query, mode, ranker, results_count, latency_ms) VALUES (?, ?, ?, ?, ?)",
                (query.strip(), mode, ranker, results_count, latency_ms),
            )
            return cur.lastrowid

    def record_click(self, history_id: int, doc_id: int) -> None:
        with get_connection(self.db_path) as conn:
            if conn.execute("SELECT 1 FROM documents WHERE id = ?", (doc_id,)).fetchone() is None:
                raise NotFoundError(f"Document {doc_id} not found")
            cur = conn.execute("UPDATE search_history SET clicked_doc_id = ? WHERE id = ?", (doc_id, history_id))
            if cur.rowcount == 0:
                raise NotFoundError(f"Search {history_id} not found")

    def recent(self, limit: int = 10) -> list[dict]:
        """Most recent DISTINCT queries (case-insensitive)."""
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """SELECT query, MAX(created_at) AS last_searched, COUNT(*) AS times,
                          (SELECT results_count FROM search_history h2
                            WHERE lower(h2.query) = lower(h.query) ORDER BY h2.id DESC LIMIT 1) AS results_count
                   FROM search_history h GROUP BY lower(query) ORDER BY last_searched DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def popular(self, limit: int = 10) -> list[dict]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """SELECT lower(query) AS query, COUNT(*) AS count, ROUND(AVG(latency_ms), 2) AS avg_latency_ms,
                          SUM(clicked_doc_id IS NOT NULL) AS clicks
                   FROM search_history GROUP BY lower(query) ORDER BY count DESC, query LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def summary(self) -> dict:
        with get_connection(self.db_path) as conn:
            r = conn.execute(
                """SELECT COUNT(*) AS searches, COUNT(DISTINCT lower(query)) AS unique_queries,
                          ROUND(AVG(latency_ms), 2) AS avg_latency_ms,
                          SUM(results_count = 0) AS zero_result_searches,
                          SUM(clicked_doc_id IS NOT NULL) AS clicks
                   FROM search_history"""
            ).fetchone()
        return {k: (r[k] or 0) for k in r.keys()}

    def clear(self) -> int:
        with get_connection(self.db_path) as conn:
            return conn.execute("DELETE FROM search_history").rowcount
