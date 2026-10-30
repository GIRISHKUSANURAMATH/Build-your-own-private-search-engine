"""
HTML extraction: turn a web page into searchable text plus metadata.

A web page is mostly NOT content: scripts, styles, menus, cookie banners,
footers. If we indexed all of it, every page on a site would match "Home",
"About", "Privacy Policy", and the real article would be drowned out.

STEPS
-----
1. Parse with BeautifulSoup (Python's built-in html.parser backend: no
   compiled dependency, tolerant of broken HTML).
2. Read metadata from <head>: title, description, author, language, canonical link.
3. Delete elements that are never content:
     script, style, noscript, template, svg, canvas, iframe, form controls...
     anything hidden (hidden attribute, aria-hidden="true", display:none)
4. Delete boilerplate (page chrome):
     nav, aside, page-level header/footer, ARIA landmark roles
     (navigation, banner, contentinfo, complementary, search), and elements
     whose class/id is exactly a known boilerplate name (sidebar, breadcrumb...)
5. Pick the main content root: <main> / role="main" > the largest <article> > <body>.
   If that root is suspiciously short, fall back to <body>.
6. Walk the remaining tree and emit text, inserting line breaks at block
   elements (<p>, <li>, <h1>...) so paragraphs survive.
7. Clean the text (cleaner.py).

This is a heuristic, like every real-world content extractor. It works well on
articles and documentation; it cannot be perfect on arbitrary layouts.

SECURITY
--------
* Input larger than `max_bytes` is truncated before parsing (a 2 GB "page"
  must not exhaust memory). `truncated=True` tells the caller.
* Parsing never executes scripts or fetches anything: BeautifulSoup only
  builds a tree. Extracted text is plain text; the frontend must still escape
  it when rendering.
"""

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Comment, Doctype, NavigableString, ProcessingInstruction, Tag
from bs4.element import CData, Declaration

from app.indexing.cleaner import clean_text

DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 5 MB of HTML is far beyond any normal page

NEVER_CONTENT = (
    "script", "style", "noscript", "template", "svg", "math", "canvas", "iframe",
    "object", "embed", "video", "audio", "map", "button", "input", "select",
    "textarea", "option", "label", "dialog",
)
BOILERPLATE_TAGS = ("nav", "aside")
PAGE_LEVEL_TAGS = ("header", "footer")  # boilerplate only outside <article>/<main>
BOILERPLATE_ROLES = {"navigation", "banner", "contentinfo", "complementary", "search"}
# Exact class/id tokens only — substring matching would delete real content
# (e.g. "comment" appears in code documentation).
BOILERPLATE_NAMES = {
    "sidebar", "breadcrumb", "breadcrumbs", "cookie-banner", "cookie-notice", "cookie-consent",
    "advertisement", "ads", "ad-banner", "share", "social", "social-share", "skip-link",
    "site-header", "site-footer", "menu", "navbar",
}
BLOCK_TAGS = {
    "address", "article", "blockquote", "br", "dd", "details", "div", "dl", "dt", "figcaption",
    "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "li", "main", "ol", "p", "pre",
    "section", "summary", "table", "tbody", "thead", "tfoot", "tr", "ul", "body", "html",
}
CELL_TAGS = {"td", "th"}
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)
_SKIP_STRINGS = (Comment, Doctype, ProcessingInstruction, Declaration, CData)
MIN_MAIN_CONTENT_CHARS = 80


@dataclass(frozen=True)
class ExtractedDocument:
    title: str
    text: str
    description: str | None = None
    author: str | None = None
    language: str | None = None
    canonical_url: str | None = None  # raw href of <link rel="canonical">; resolved/used in Phase 9
    headings: list[str] = field(default_factory=list)
    truncated: bool = False


class UnsupportedContentType(ValueError):
    pass


# ---------------------------------------------------------------- helpers
def _attr(tag: Tag, name: str) -> str:
    value = tag.get(name)
    if isinstance(value, list):  # bs4 returns multi-valued attrs (class, rel) as lists
        return " ".join(value)
    return value or ""


def _meta(soup: BeautifulSoup, *, name: str | None = None, prop: str | None = None) -> str | None:
    for tag in soup.find_all("meta"):
        matches = (name is not None and _attr(tag, "name").lower() == name) or (
            prop is not None and _attr(tag, "property").lower() == prop
        )
        content = clean_text(_attr(tag, "content")) if matches else ""
        if content:
            return content
    return None


def _inline_text(tag: Tag | None) -> str:
    if tag is None:
        return ""
    return clean_text(" ".join(tag.stripped_strings)).replace("\n", " ")


def _is_hidden(tag: Tag) -> bool:
    return (
        tag.has_attr("hidden")
        or _attr(tag, "aria-hidden").lower() == "true"
        or bool(_HIDDEN_STYLE.search(_attr(tag, "style")))
    )


def _is_boilerplate_name(tag: Tag) -> bool:
    names = set(_attr(tag, "class").lower().split()) | {_attr(tag, "id").lower()}
    return bool(names & BOILERPLATE_NAMES)


def _inside_content(tag: Tag) -> bool:
    """Is this element inside the main content (an <article>, <main> or role="main")?"""
    if tag.find_parent(["article", "main"]) is not None:
        return True
    return any(_attr(parent, "role").lower() == "main" for parent in tag.parents if isinstance(parent, Tag))


def _remove_noise(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(NEVER_CONTENT):
        tag.decompose()
    for tag in soup.find_all(True):
        if tag.decomposed:  # already removed with an ancestor
            continue
        role = _attr(tag, "role").lower()
        if (
            _is_hidden(tag)
            or tag.name in BOILERPLATE_TAGS
            or (tag.name in PAGE_LEVEL_TAGS and not _inside_content(tag))
            or role in BOILERPLATE_ROLES
            or _is_boilerplate_name(tag)
        ):
            tag.decompose()


def _block_text(root: Tag) -> str:
    """Text of `root` with a newline at every block element boundary."""
    parts: list[str] = []
    for node in root.descendants:
        if isinstance(node, NavigableString):
            if not isinstance(node, _SKIP_STRINGS):
                parts.append(str(node))
        elif isinstance(node, Tag):
            if node.name in BLOCK_TAGS:
                parts.append("\n")
            elif node.name in CELL_TAGS:
                parts.append(" ")
    return clean_text("".join(parts))


def _choose_root(soup: BeautifulSoup) -> Tag:
    main = soup.find("main") or soup.find(attrs={"role": "main"})
    if main is not None:
        return main
    articles = soup.find_all("article")
    if articles:
        return max(articles, key=lambda a: len(a.get_text()))
    return soup.body or soup


# ---------------------------------------------------------------- public API
def extract_html(html: str | bytes, *, max_bytes: int = DEFAULT_MAX_BYTES) -> ExtractedDocument:
    """Extract title, main text and metadata from an HTML page."""
    truncated = len(html) > max_bytes
    if truncated:
        html = html[:max_bytes]

    # bytes: BeautifulSoup detects the encoding (BOM, <meta charset>, or a guess)
    soup = BeautifulSoup(html, "html.parser")

    # --- metadata (read BEFORE removing anything) ---
    title = _inline_text(soup.title) or (_meta(soup, prop="og:title") or "")
    description = _meta(soup, name="description") or _meta(soup, prop="og:description")
    author = _meta(soup, name="author") or _meta(soup, prop="article:author")
    html_tag = soup.find("html")
    language = (_attr(html_tag, "lang") if isinstance(html_tag, Tag) else "") or ""
    if not language:
        for tag in soup.find_all("meta"):
            if _attr(tag, "http-equiv").lower() == "content-language":
                language = _attr(tag, "content")
                break
    language = language.strip().lower() or None
    canonical = None
    for link in soup.find_all("link"):
        if "canonical" in _attr(link, "rel").lower().split() and _attr(link, "href").strip():
            canonical = _attr(link, "href").strip()
            break

    if soup.head:
        soup.head.decompose()
    _remove_noise(soup)

    root = _choose_root(soup)
    text = _block_text(root)
    if len(text) < MIN_MAIN_CONTENT_CHARS and soup.body is not None and root is not soup.body:
        body_text = _block_text(soup.body)
        if len(body_text) > len(text):
            text = body_text

    headings = [h for h in (_inline_text(t) for t in root.find_all(["h1", "h2", "h3"])) if h]
    if not title and headings:
        title = headings[0]

    return ExtractedDocument(
        title=title,
        text=text,
        description=description,
        author=author,
        language=language,
        canonical_url=canonical,
        headings=headings,
        truncated=truncated,
    )


def extract_text(content: str | bytes, *, max_bytes: int = DEFAULT_MAX_BYTES) -> ExtractedDocument:
    """Plain-text documents: clean only. The first non-empty line becomes the title."""
    truncated = len(content) > max_bytes
    if truncated:
        content = content[:max_bytes]
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    text = clean_text(content)
    first_line = next((line for line in text.split("\n") if line.strip()), "")
    return ExtractedDocument(title=first_line[:200], text=text, truncated=truncated)


def extract_document(content: str | bytes, content_type: str = "text/html", **kwargs) -> ExtractedDocument:
    """Dispatch on MIME type ('text/html; charset=utf-8' -> 'text/html')."""
    mime = content_type.split(";")[0].strip().lower()
    if mime in ("text/html", "application/xhtml+xml"):
        return extract_html(content, **kwargs)
    if mime in ("text/plain", "text/markdown"):
        return extract_text(content, **kwargs)
    raise UnsupportedContentType(f"Cannot extract text from {content_type!r}")
