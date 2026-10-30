import { useState } from "react";
import { api } from "../api.js";
import Highlighted from "./Highlighted.jsx";

export default function ResultCard({ result, historyId, showExplain }) {
  const [open, setOpen] = useState(false);

  function track() {
    if (historyId) api.click(historyId, result.id).catch(() => {});
  }

  return (
    <article className="result">
      <a className="result__url" href={result.url} target="_blank" rel="noopener noreferrer" onClick={track}>
        {result.url}
      </a>
      <h2 className="result__title">
        <a href={`#/doc/${result.id}`} onClick={track}>
          <Highlighted segments={result.title_segments} fallback={result.title} />
        </a>
      </h2>
      <p className="result__snippet">
        <Highlighted segments={result.snippet.segments} fallback="No text preview available." />
      </p>
      <div className="result__meta">
        <span title="Relevance score from the selected ranking algorithm">Score {result.score.toFixed(2)}</span>
        {result.matched_terms.length > 0 && <span>Matched: {result.matched_terms.join(", ")}</span>}
        {result.author && <span>By {result.author}</span>}
        {showExplain && result.explain && (
          <button type="button" className="linkish" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
            {open ? "Hide score details" : "Why this score?"}
          </button>
        )}
      </div>
      {open && result.explain && (
        <table className="explain">
          <thead>
            <tr>
              <th>Term</th><th>Field</th><th>tf</th><th>df</th><th>idf</th><th>Contribution</th>
            </tr>
          </thead>
          <tbody>
            {result.explain.map((e, i) => (
              <tr key={i}>
                <td>{e.term}</td>
                <td>{e.field}{e.boost !== 1 ? ` ×${e.boost}` : ""}</td>
                <td>{e.tf}</td>
                <td>{e.df}/{e.N}</td>
                <td>{e.idf}</td>
                <td>{e.score}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </article>
  );
}
