// All backend calls go through this file so the base URL lives in one place.
// Default: "/api" on this origin, forwarded to the backend by Vite's proxy
// (vite.config.js). To call the API directly instead, set in frontend/.env:
//   VITE_API_URL=http://127.0.0.1:8000
export const API_URL = import.meta.env.VITE_API_URL ?? "/api";

async function request(path, { method = "GET", body, params } = {}) {
  const url = new URL(API_URL + path, window.location.origin);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
    }
  }
  let res;
  try {
    res = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error("Cannot reach the PySearch API. Is the backend running on port 8000?");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ")
      : data.detail;
    throw new Error(detail || `Request failed (HTTP ${res.status})`);
  }
  return data;
}

export const api = {
  health: () => request("/health"),
  search: (params) => request("/search", { params }),
  suggest: (q) => request("/search/suggest", { params: { q } }),
  history: (limit = 8) => request("/search/history", { params: { limit } }),
  clearHistory: () => request("/search/history", { method: "DELETE" }),
  click: (history_id, doc_id) => request("/search/click", { method: "POST", body: { history_id, doc_id } }),
  document: (id) => request(`/documents/${id}`),
  documents: (params) => request("/documents", { params }),
  addDocument: (body) => request("/documents", { method: "POST", body }),
  deleteDocument: (id) => request(`/documents/${id}`, { method: "DELETE" }),
  loadSample: () => request("/documents/sample", { method: "POST" }),
  buildIndex: () => request("/index/build", { method: "POST" }),
  rebuildIndex: () => request("/index/rebuild", { method: "POST" }),
  stats: () => request("/stats"),
  startCrawl: (body) => request("/crawl", { method: "POST", body }),
  crawlStatus: (job_id) => request("/crawl/status", { params: { job_id } }),
  crawlJobs: () => request("/crawl/jobs"),
  cancelCrawl: (id) => request(`/crawl/${id}/cancel`, { method: "POST" }),
};
