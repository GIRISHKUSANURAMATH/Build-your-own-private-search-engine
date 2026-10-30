// Horizontal bars for small ranked lists (top terms, top queries, domains).
export default function BarList({ items, label, value, empty = "Nothing yet." }) {
  if (!items || items.length === 0) return <p className="muted">{empty}</p>;
  const max = Math.max(...items.map(value), 1);
  return (
    <ol className="barlist">
      {items.map((it, i) => (
        <li key={i}>
          <span className="barlist__label">{label(it)}</span>
          <span className="barlist__bar" style={{ "--w": `${(value(it) / max) * 100}%` }} aria-hidden="true" />
          <span className="barlist__value">{value(it)}</span>
        </li>
      ))}
    </ol>
  );
}
