import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { searchHref } from "../router.js";
import BarList from "../components/BarList.jsx";

function bytes(n) {
  if (n == null) return "–";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function when(iso) {
  return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "never";
}

export default function Admin({ refreshStats }) {
  const [stats, setStats] = useState(null);
  const [docs, setDocs] = useState({ documents: [], total: 0 });
  const [jobs, setJobs] = useState([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  const [offset, setOffset] = useState(0);
  const poll = useRef();

  const load = useCallback(async () => {
    try {
      const [s, d, j] = await Promise.all([api.stats(), api.documents({ limit: 15, offset }), api.crawlJobs()]);
      setStats(s);
      setDocs(d);
      setJobs(j.jobs);
      setBusy(j.busy);
      refreshStats?.();
    } catch (e) {
      setMessage({ kind: "error", text: e.message });
    }
  }, [offset, refreshStats]);

  useEffect(() => {
    load();
  }, [load]);

  // poll while a crawl is running
  useEffect(() => {
    clearInterval(poll.current);
    if (busy) poll.current = setInterval(load, 2000);
    return () => clearInterval(poll.current);
  }, [busy, load]);

  async function run(action, success) {
    setMessage(null);
    try {
      const r = await action();
      setMessage({ kind: "ok", text: typeof success === "function" ? success(r) : success });
      await load();
    } catch (e) {
      setMessage({ kind: "error", text: e.message });
    }
  }

  return (
    <main className="admin">
      <h1>Collection and index</h1>
      {message && <p className={message.kind === "error" ? "error" : "ok"} role="status">{message.text}</p>}

      {stats && (
        <dl className="figures">
          <div><dt>Documents</dt><dd>{stats.documents}</dd></div>
          <div><dt>Indexed terms</dt><dd>{stats.index.terms}</dd></div>
          <div><dt>Sites</dt><dd>{stats.domains}</dd></div>
          <div><dt>Pages crawled</dt><dd>{stats.crawl.pages_fetched}</dd></div>
          <div><dt>Failed pages</dt><dd>{stats.crawl.pages_failed}</dd></div>
          <div><dt>Duplicates skipped</dt><dd>{stats.crawl.duplicates}</dd></div>
          <div><dt>Index size</dt><dd>{bytes(stats.index_size_bytes)}</dd></div>
          <div><dt>Last indexed</dt><dd className="small">{when(stats.index.last_indexed_at)}</dd></div>
        </dl>
      )}

      <section className="panel">
        <h2>Crawl a website</h2>
        <CrawlForm busy={busy} onStart={(body) => run(() => api.startCrawl(body).then((r) => { setBusy(true); return r; }),
          (r) => `Crawl started: job ${r.job.id}, up to ${r.limits.max_pages} pages, depth ${r.limits.max_depth}.`)} />
        {jobs.length > 0 && (
          <table className="table">
            <thead>
              <tr><th>Job</th><th>Start URL</th><th>Status</th><th>Found</th><th>Fetched</th><th>Failed</th><th /></tr>
            </thead>
            <tbody>
              {jobs.slice(0, 6).map((j) => (
                <tr key={j.id}>
                  <td>{j.id}</td>
                  <td className="truncate" title={j.seed_url}>{j.seed_url}</td>
                  <td><span className={`status status--${j.status}`}>{j.status}</span>{j.error && <small className="muted"> {j.error}</small>}</td>
                  <td>{j.pages_discovered}</td>
                  <td>{j.pages_fetched}</td>
                  <td>{j.pages_failed}</td>
                  <td>{(j.status === "running" || j.status === "pending") && (
                    <button type="button" className="linkish" onClick={() => run(() => api.cancelCrawl(j.id), `Job ${j.id} cancelled.`)}>Cancel</button>
                  )}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel">
        <h2>Index</h2>
        <p className="muted">The index updates automatically when documents are added. Rebuild after changing analyzer settings or editing the database directly.</p>
        <div className="actions">
          <button type="button" className="button" onClick={() => run(api.buildIndex, (r) => `Indexed ${r.indexed} new or changed documents.`)}>Index new documents</button>
          <button type="button" className="button button--quiet" onClick={() => run(api.rebuildIndex, (r) => `Rebuilt the index from ${r.indexed} documents in ${r.seconds} s.`)}>Rebuild index</button>
          <button type="button" className="button button--quiet" onClick={() => run(api.loadSample, (r) => `Sample collection: ${r.created} added, ${r.updated} updated.`)}>Load sample collection</button>
        </div>
      </section>

      {stats && (
        <div className="columns">
          <section className="panel">
            <h2>Most common terms</h2>
            <BarList items={stats.index.top_terms.slice(0, 10)}
                     label={(t) => <span title={`Index term (stem): ${t.term}`}>{t.word}</span>} value={(t) => t.df}
                     empty="The index is empty." />
            <p className="muted small">Bar length = number of documents containing the word. Hover a word to see the stem stored in the index.</p>
          </section>
          <section className="panel">
            <h2>Most searched</h2>
            <BarList items={stats.top_queries.slice(0, 10)}
                     label={(q) => <a href={searchHref(q.query)}>{q.query}</a>} value={(q) => q.count}
                     empty="No searches yet." />
            <p className="muted small">
              {stats.search.searches} searches, {stats.search.zero_result_searches} with no results,
              average {stats.search.avg_latency_ms} ms.
            </p>
          </section>
          <section className="panel">
            <h2>Documents per site</h2>
            <BarList items={stats.top_domains} label={(d) => d.domain} value={(d) => d.documents} empty="No documents yet." />
          </section>
        </div>
      )}

      <section className="panel">
        <h2>Add a document</h2>
        <AddDocumentForm onAdd={(body) => run(() => api.addDocument(body),
          (r) => `${r.created ? "Added" : "Updated"} “${r.document.title || r.document.url}” and indexed it.`)} />
      </section>

      <section className="panel">
        <h2>Documents ({docs.total})</h2>
        {docs.documents.length === 0 ? <p className="muted">No documents yet.</p> : (
          <table className="table">
            <thead><tr><th>Title</th><th>Site</th><th>Added</th><th /></tr></thead>
            <tbody>
              {docs.documents.map((d) => (
                <tr key={d.id}>
                  <td><a href={`#/doc/${d.id}`}>{d.title || d.url}</a></td>
                  <td>{d.domain}</td>
                  <td className="nowrap">{when(d.created_at)}</td>
                  <td>
                    <button type="button" className="linkish danger"
                            onClick={() => window.confirm(`Delete “${d.title || d.url}” from the collection and index?`) &&
                              run(() => api.deleteDocument(d.id), "Document deleted.")}>
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {docs.total > 15 && (
          <nav className="pager">
            <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 15))}>Previous</button>
            <span>{offset + 1}–{Math.min(offset + 15, docs.total)} of {docs.total}</span>
            <button type="button" disabled={offset + 15 >= docs.total} onClick={() => setOffset(offset + 15)}>Next</button>
          </nav>
        )}
      </section>
    </main>
  );
}

function CrawlForm({ busy, onStart }) {
  const [url, setUrl] = useState("");
  const [depth, setDepth] = useState(1);
  const [pages, setPages] = useState(20);
  return (
    <form className="form form--row" onSubmit={(e) => { e.preventDefault(); onStart({ url, max_depth: Number(depth), max_pages: Number(pages) }); }}>
      <label className="grow">Start URL<input type="url" required value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://docs.python.org/3/tutorial/" /></label>
      <label>Depth<input type="number" min="0" max="5" value={depth} onChange={(e) => setDepth(e.target.value)} /></label>
      <label>Max pages<input type="number" min="1" max="5000" value={pages} onChange={(e) => setPages(e.target.value)} /></label>
      <button type="submit" className="button" disabled={busy}>{busy ? "Crawling…" : "Start crawl"}</button>
      <p className="muted small full">Stays on the start URL's site, follows robots.txt and waits between requests. Server settings cap depth and page count.</p>
    </form>
  );
}

function AddDocumentForm({ onAdd }) {
  const [form, setForm] = useState({ url: "", title: "", author: "", format: "text", content: "" });
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  return (
    <form className="form" onSubmit={(e) => { e.preventDefault(); onAdd({ ...form, title: form.title || null, author: form.author || null }); }}>
      <div className="form--row">
        <label className="grow">URL<input type="url" required value={form.url} onChange={set("url")} placeholder="https://example.com/my-notes" /></label>
        <label className="grow">Title<input value={form.title} onChange={set("title")} placeholder="Taken from the HTML if empty" /></label>
        <label>Author<input value={form.author} onChange={set("author")} /></label>
        <label>Format<select value={form.format} onChange={set("format")}><option value="text">Plain text</option><option value="html">HTML</option></select></label>
      </div>
      <label>Content<textarea required rows="5" value={form.content} onChange={set("content")} placeholder="Paste text or HTML" /></label>
      <button type="submit" className="button">Add and index</button>
    </form>
  );
}
