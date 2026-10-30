import { useCallback, useEffect, useState } from "react";
import { API_URL, api } from "./api.js";
import { useRoute } from "./router.js";
import Admin from "./pages/Admin.jsx";
import DocumentView from "./pages/DocumentView.jsx";
import Home from "./pages/Home.jsx";
import Results from "./pages/Results.jsx";

function StatusDot({ health, error }) {
  const ok = health?.status === "ok";
  const text = error ? "API offline" : !health ? "Connecting…" : ok ? "API connected" : "API degraded";
  return (
    <span className={`status-dot ${error ? "is-down" : ok ? "is-ok" : ""}`} title={error ? `${error} (${API_URL})` : `v${health?.version ?? ""}`}>
      {text}
    </span>
  );
}

export default function App() {
  const { path, params } = useRoute();
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState(null);
  const [stats, setStats] = useState(null);
  const [toast, setToast] = useState(null);

  const refreshStats = useCallback(() => {
    api.stats().then(setStats).catch(() => {});
  }, []);

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setHealthError(e.message));
    refreshStats();
    const onError = (e) => setToast(e.detail);
    window.addEventListener("pysearch:error", onError);
    return () => window.removeEventListener("pysearch:error", onError);
  }, [refreshStats]);

  let page;
  if (path === "/search") page = <Results params={params} />;
  else if (path.startsWith("/doc/")) page = <DocumentView id={path.slice(5)} />;
  else if (path === "/admin") page = <Admin refreshStats={refreshStats} />;
  else page = <Home stats={stats} refreshStats={refreshStats} />;

  return (
    <div className="app">
      <header className="topbar">
        {path !== "/" ? <a className="topbar__brand" href="#/">PySearch</a> : <span />}
        <nav className="topbar__nav">
          <a href="#/" aria-current={path === "/" ? "page" : undefined}>Search</a>
          <a href="#/admin" aria-current={path === "/admin" ? "page" : undefined}>Admin</a>
          <StatusDot health={health} error={healthError} />
        </nav>
      </header>
      {toast && (
        <p className="toast error" role="alert" onClick={() => setToast(null)}>{toast}</p>
      )}
      {page}
      <footer className="footer">Runs entirely on your computer. No search data leaves this machine.</footer>
    </div>
  );
}
