// Tiny hash router (no dependency): #/  #/search?q=...  #/doc/12  #/admin
// Hash routes work with any static file server and need no server config.
import { useEffect, useState } from "react";

function parse() {
  const raw = window.location.hash.replace(/^#/, "") || "/";
  const [path, query = ""] = raw.split("?");
  return { path, params: Object.fromEntries(new URLSearchParams(query)) };
}

export function useRoute() {
  const [route, setRoute] = useState(parse);
  useEffect(() => {
    const onChange = () => setRoute(parse());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export function navigate(path, params) {
  const qs = params
    ? "?" + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== "" && v !== null))
    : "";
  window.location.hash = path + qs;
}

export function searchHref(q, extra = {}) {
  return "#/search?" + new URLSearchParams({ q, ...extra });
}
