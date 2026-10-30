import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

const MODES = [
  { id: "basic", label: "Any words", hint: "Ranked: documents with more of your words come first" },
  { id: "boolean", label: "All words", hint: "Every word must appear. AND, OR, NOT and (brackets) also work" },
  { id: "phrase", label: "Exact phrase", hint: "The words must appear together, in this order" },
  { id: "fuzzy", label: "Typo tolerant", hint: "Also matches words with small spelling differences" },
];

export default function SearchBox({ initial = "", mode = "basic", ranker = "bm25", onSearch, size = "large", autoFocus }) {
  const [q, setQ] = useState(initial);
  const [m, setM] = useState(mode);
  const [r, setR] = useState(ranker);
  const [suggestions, setSuggestions] = useState([]);
  const [active, setActive] = useState(-1);
  const [open, setOpen] = useState(false);
  const timer = useRef();
  const listId = `suggest-${size}`;

  useEffect(() => setQ(initial), [initial]);
  useEffect(() => setM(mode), [mode]);
  useEffect(() => setR(ranker), [ranker]);

  function fetchSuggestions(text) {
    clearTimeout(timer.current);
    if (!text.trim()) {
      setSuggestions([]);
      return;
    }
    timer.current = setTimeout(() => {
      api.suggest(text).then((d) => {
        setSuggestions(d.suggestions);
        setActive(-1);
      }).catch(() => setSuggestions([]));
    }, 140);
  }

  function submit(text = q, opts = {}) {
    const query = text.trim();
    if (!query) return;
    setOpen(false);
    onSearch({ q: query, mode: opts.mode ?? m, ranker: opts.ranker ?? r });
  }

  function onKeyDown(e) {
    if (!open || suggestions.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => (a + 1) % suggestions.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => (a <= 0 ? suggestions.length - 1 : a - 1));
    } else if (e.key === "Escape") {
      setOpen(false);
    } else if (e.key === "Enter" && active >= 0) {
      e.preventDefault();
      setQ(suggestions[active]);
      submit(suggestions[active]);
    }
  }

  return (
    <div className={`searchbox searchbox--${size}`}>
      <form
        className="searchbox__field"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <svg className="searchbox__icon" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="11" cy="11" r="7" />
          <path d="M20 20l-3.5-3.5" />
        </svg>
        <input
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setOpen(true);
            fetchSuggestions(e.target.value);
          }}
          onKeyDown={onKeyDown}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 120)}
          placeholder="Search your collection"
          aria-label="Search query"
          role="combobox"
          aria-expanded={open && suggestions.length > 0}
          aria-controls={listId}
          aria-autocomplete="list"
          autoFocus={autoFocus}
          maxLength={500}
        />
        <button type="submit">Search</button>
        {open && suggestions.length > 0 && (
          <ul className="suggest" id={listId} role="listbox">
            {suggestions.map((s, i) => (
              <li
                key={s}
                role="option"
                aria-selected={i === active}
                className={i === active ? "is-active" : ""}
                onMouseDown={(e) => {
                  e.preventDefault();
                  setQ(s);
                  submit(s);
                }}
              >
                {s}
              </li>
            ))}
          </ul>
        )}
      </form>

      <div className="searchbox__options">
        <div className="segmented" role="radiogroup" aria-label="Search mode">
          {MODES.map((opt) => (
            <button
              key={opt.id}
              type="button"
              role="radio"
              aria-checked={m === opt.id}
              title={opt.hint}
              onClick={() => {
                setM(opt.id);
                if (size === "compact" && q.trim()) submit(q, { mode: opt.id });
              }}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <label className="ranker">
          Ranking
          <select
            value={r}
            onChange={(e) => {
              setR(e.target.value);
              if (size === "compact" && q.trim()) submit(q, { ranker: e.target.value });
            }}
          >
            <option value="bm25">BM25</option>
            <option value="tfidf">TF-IDF</option>
          </select>
        </label>
      </div>
    </div>
  );
}
