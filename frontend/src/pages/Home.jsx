import { useEffect, useState } from "react";
import { api } from "../api.js";
import { navigate, searchHref } from "../router.js";
import SearchBox from "../components/SearchBox.jsx";

function formatWhen(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export default function Home({ stats, refreshStats }) {
  const [recent, setRecent] = useState([]);

  useEffect(() => {
    api.history(6).then((d) => setRecent(d.recent)).catch(() => {});
  }, []);

  const docs = stats?.indexed_documents ?? 0;

  return (
    <main className="home">
      <h1 className="wordmark">PySearch</h1>
      <p className="home__lede">
        {stats == null
          ? "A private search engine for your own collection."
          : docs === 0
            ? "Your collection is empty. Add documents or crawl a site to start searching."
            : `Searching ${docs} documents from ${stats.domains} ${stats.domains === 1 ? "site" : "sites"}` +
              (stats.index?.last_indexed_at ? `, last indexed ${formatWhen(stats.index.last_indexed_at)}.` : ".")}
      </p>

      <SearchBox autoFocus onSearch={(p) => navigate("/search", p)} />

      {stats != null && docs === 0 && (
        <div className="empty-actions">
          <button
            type="button"
            className="button"
            onClick={() => api.loadSample().then(refreshStats).catch((e) => alertError(e))}
          >
            Load the sample collection
          </button>
          <a className="button button--quiet" href="#/admin">Crawl a website</a>
        </div>
      )}

      {recent.length > 0 && (
        <section className="recent" aria-label="Recent searches">
          <div className="recent__head">
            <h2>Recent searches</h2>
            <button
              type="button"
              className="linkish"
              onClick={() => api.clearHistory().then(() => setRecent([]))}
            >
              Clear history
            </button>
          </div>
          <ul>
            {recent.map((r) => (
              <li key={r.query}>
                <a href={searchHref(r.query)}>{r.query}</a>
                <span className="muted">{r.results_count} results</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <details className="syntax">
        <summary>Search syntax</summary>
        <dl>
          <dt><code>python AND django</code></dt><dd>both words required</dd>
          <dt><code>python OR java</code></dt><dd>either word</dd>
          <dt><code>python NOT java</code></dt><dd>exclude a word (or <code>-java</code>)</dd>
          <dt><code>"machine learning"</code></dt><dd>exact phrase</dd>
          <dt><code>title:python</code></dt><dd>word in the title</dd>
          <dt><code>site:example.com</code></dt><dd>only pages from that site</dd>
          <dt><code>author:asha</code></dt><dd>only documents by that author</dd>
          <dt><code>machne~</code></dt><dd>allow typos in this word</dd>
        </dl>
      </details>
    </main>
  );
}

function alertError(e) {
  window.dispatchEvent(new CustomEvent("pysearch:error", { detail: e.message }));
}
