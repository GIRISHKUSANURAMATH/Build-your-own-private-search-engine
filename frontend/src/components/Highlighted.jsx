// Renders [{text, highlight}] segments from the API.
// Text is rendered as plain React text (never innerHTML), so markup inside
// crawled pages can never execute in the browser.
export default function Highlighted({ segments, fallback = "" }) {
  if (!segments || segments.length === 0) return <>{fallback}</>;
  return (
    <>
      {segments.map((s, i) => (s.highlight ? <mark key={i}>{s.text}</mark> : <span key={i}>{s.text}</span>))}
    </>
  );
}
