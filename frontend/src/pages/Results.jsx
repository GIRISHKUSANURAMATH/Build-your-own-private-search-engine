import { useEffect, useState } from "react";
import { api } from "../api.js";
import { navigate, searchHref } from "../router.js";
import ResultCard from "../components/ResultCard.jsx";
import SearchBox from "../components/SearchBox.jsx";

export default function Results({ params }) {
  const q = params.q || "";
  const mode = params.mode || "basic";
  const ranker = params.ranker || "bm25";
  const page = Number(params.page || 1);
  const explain = params.explain === "1";

  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!q) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .search({ q, mode, ranker, page, explain: explain ? "true" : undefined })
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [q, mode, ranker, page, explain]);

  const go = (changes) => navigate("/search", { q, mode, ranker, explain: explain ? "1" : undefined, ...changes });

  return (
    <main className="results-page">
      <div className="results-page__bar">
        <SearchBox size="compact" initial={q} mode={mode} ranker={ranker}
                   onSearch={(p) => navigate("/search", { ...p, explain: explain ? "1" : undefined })} />
      </div>

      <div className="results-page__body">
        <section className="results" aria-live="polite" aria-busy={loading}>
          {error && <p className="error" role="alert">{error}</p>}
          {!error && data && (
            <>
              <p className="results__count">
                {data.total === 0
                  ? "No documents match."
                  : `${data.total} ${data.total === 1 ? "result" : "results"} in ${data.took_ms} ms`}
              </p>
              {data.did_you_mean && (
                <p className="dym">
                  Did you mean <a href={searchHref(data.did_you_mean, { mode, ranker })}>{data.did_you_mean}</a>?
                </p>
              )}
              {data.warnings.map((w) => <p key={w} className="note">{w}</p>)}
              {Object.keys(data.expansions).length > 0 && (
                <p className="note">
                  Also matched:{" "}
                  {Object.entries(data.expansions).map(([w, alts]) => `${w} → ${alts.join(", ")}`).join("; ")}
                </p>
              )}
              {data.total === 0 && (
                <div className="noresults">
                  <p>Try fewer words, the <b>Typo tolerant</b> mode, or <b>Any words</b> instead of <b>All words</b>.</p>
                </div>
              )}
              {data.results.map((r) => (
                <ResultCard key={r.id} result={r} historyId={data.history_id} showExplain={explain} />
              ))}
              {data.pages > 1 && (
                <nav className="pager" aria-label="Result pages">
                  <button type="button" disabled={page <= 1} onClick={() => go({ page: page - 1 })}>Previous</button>
                  <span>Page {page} of {data.pages}</span>
                  <button type="button" disabled={page >= data.pages} onClick={() => go({ page: page + 1 })}>Next</button>
                </nav>
              )}
            </>
          )}
          {loading && !data && <p className="muted">Searching…</p>}
        </section>

        {data && (
          <aside className="query-panel" aria-label="How this query was understood">
            <h2>How PySearch read your query</h2>
            <code className="parsed">{data.parsed}</code>
            <dl>
              <dt>Mode</dt><dd>{data.mode}</dd>
              <dt>Ranking</dt><dd>{data.ranker === "bm25" ? "BM25 (k1 1.2, b 0.75)" : "TF-IDF"}</dd>
            </dl>
            <label className="check">
              <input type="checkbox" checked={explain} onChange={(e) => go({ explain: e.target.checked ? "1" : undefined })} />
              Show score details
            </label>
            <button type="button" className="linkish"
                    onClick={() => go({ ranker: ranker === "bm25" ? "tfidf" : "bm25", page: 1 })}>
              Compare with {ranker === "bm25" ? "TF-IDF" : "BM25"}
            </button>
          </aside>
        )}
      </div>
    </main>
  );
}
