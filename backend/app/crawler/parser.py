"""
Link extraction and robots meta tags (crawler side of HTML parsing).

Text extraction lives in indexing/html_extractor.py and deliberately throws
away navigation. For crawling we want the opposite: menus are exactly where
the links to other pages are. So links are read from the FULL page.

Rules:
* <base href="..."> changes how relative links resolve.
* Only http/https links; skip mailto:, javascript:, tel:, data:, and "#top"-only links.
* rel="nofollow" links are not followed.
* Every link is normalized (utils/urls.py), so duplicates collapse.
* <meta name="robots" content="noindex, nofollow"> is honoured:
    noindex  -> do not store/index this page
    nofollow -> do not follow any link on it
"""

from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.utils.urls import InvalidURLError, normalize_url

SKIP_SCHEMES = ("mailto:", "javascript:", "tel:", "data:", "ftp:", "file:")
MAX_LINKS_PER_PAGE = 500


@dataclass
class PageLinks:
    links: list[str] = field(default_factory=list)
    noindex: bool = False
    nofollow: bool = False
    canonical: str | None = None


def extract_links(html: str | bytes, base_url: str) -> PageLinks:
    soup = BeautifulSoup(html, "html.parser")
    result = PageLinks()

    for meta in soup.find_all("meta"):
        if str(meta.get("name", "")).lower() in ("robots", "pysearchbot"):
            directives = {d.strip().lower() for d in str(meta.get("content", "")).split(",")}
            result.noindex |= "noindex" in directives or "none" in directives
            result.nofollow |= "nofollow" in directives or "none" in directives

    base_tag = soup.find("base", href=True)
    base = urljoin(base_url, base_tag["href"]) if base_tag else base_url

    for link in soup.find_all("link", href=True):
        rel = link.get("rel") or []
        rel = rel if isinstance(rel, list) else [rel]
        if "canonical" in [r.lower() for r in rel]:
            try:
                result.canonical = normalize_url(urljoin(base, link["href"]))
            except InvalidURLError:
                pass
            break

    if result.nofollow:
        return result

    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.lower().startswith(SKIP_SCHEMES):
            continue
        rel = a.get("rel") or []
        rel = rel if isinstance(rel, list) else [rel]
        if "nofollow" in [r.lower() for r in rel]:
            continue
        try:
            url = normalize_url(urljoin(base, href))
        except (InvalidURLError, ValueError):
            continue
        if url not in seen:
            seen.add(url)
            result.links.append(url)
            if len(result.links) >= MAX_LINKS_PER_PAGE:
                break
    return result
