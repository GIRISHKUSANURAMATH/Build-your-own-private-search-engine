import { useEffect, useState } from "react";
import { api } from "../api.js";

export default function DocumentView({ id }) {
  const [doc, setDoc] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    setDoc(null);
    setError(null);
    api.document(id).then(setDoc).catch((e) => setError(e.message));
  }, [id]);

  if (error) return <main className="docview"><p className="error" role="alert">{error}</p></main>;
  if (!doc) return <main className="docview"><p className="muted">Loading…</p></main>;

  return (
    <main className="docview">
      <button type="button" className="linkish" onClick={() => window.history.back()}>Back to results</button>
      <h1 className="docview__title">{doc.title || doc.url}</h1>
      <a className="docview__url" href={doc.url} target="_blank" rel="noopener noreferrer">{doc.url}</a>
      <dl className="docview__meta">
        {doc.author && (<><dt>Author</dt><dd>{doc.author}</dd></>)}
        <dt>Site</dt><dd>{doc.domain}</dd>
        {doc.language && (<><dt>Language</dt><dd>{doc.language}</dd></>)}
        <dt>Index terms</dt><dd>{doc.term_count}</dd>
        <dt>Added</dt><dd>{new Date(doc.created_at).toLocaleString()}</dd>
      </dl>
      {doc.top_terms.length > 0 && (
        <section className="docview__terms" aria-label="Most frequent index terms">
          <h2>Most frequent terms (forward index)</h2>
          <p>
            {doc.top_terms.map((t) => (
              <span key={t.term} className="term">{t.term} <small>{t.tf}</small></span>
            ))}
          </p>
        </section>
      )}
      <article className="docview__content">{doc.content}</article>
    </main>
  );
}
