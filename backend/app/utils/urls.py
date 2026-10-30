"""
URL normalization (canonicalization).

WHY
---
The same page can be written many ways:

    HTTP://Example.COM:80/a/./b/../c?b=2&a=1#section
    http://example.com/a/c?a=1&b=2

If we treat these as different, the crawler fetches the page twice and the
index stores it twice. Normalization rewrites a URL into ONE standard form so
"have I seen this?" becomes a simple string comparison (and a UNIQUE index).

RULES (each is safe: it never changes which resource the URL points to)
-----
 1. Only http/https are accepted (no file:, ftp:, javascript:, data:).
 2. URLs containing a username/password are rejected (never store credentials).
 3. Scheme and host are lower-cased (they are case-insensitive by spec).
 4. Trailing dot on the host is removed ("example.com." -> "example.com").
 5. Internationalized hosts are converted to ASCII punycode.
 6. Default ports are dropped (:80 for http, :443 for https).
 7. Empty path becomes "/".
 8. Dot segments are resolved ("/a/./b/../c" -> "/a/c"), RFC 3986 §5.2.4.
 9. Percent-encoding is normalized: hex digits upper-cased, and characters
    that never needed encoding ("%7E" -> "~") are decoded.
10. Fragment ("#section") is removed: it is never sent to the server.
11. Tracking parameters (utm_*, gclid, fbclid, ...) are removed.
12. Remaining query parameters are sorted by name.

DELIBERATELY NOT DONE (would be unsafe in general)
---------------------
* Lower-casing the path: "/Docs" and "/docs" can be different pages.
* Removing a trailing slash: "/docs" and "/docs/" can be different pages.
* Stripping "www.": www.example.com and example.com can be different servers.
* Removing "index.html": not guaranteed to be the default document.
"""

import re
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

ALLOWED_SCHEMES = {"http", "https"}
DEFAULT_PORTS = {"http": 80, "https": 443}

TRACKING_PARAMS = {"gclid", "fbclid", "dclid", "msclkid", "mc_cid", "mc_eid", "yclid", "_ga"}
TRACKING_PREFIXES = ("utm_",)

# RFC 3986 "unreserved" characters never need percent-encoding.
_UNRESERVED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
# Characters allowed to appear literally in a path (unreserved + sub-delims + ':' '@' '/' + '%').
_PATH_SAFE = "/:@!$&'()*+,;=-._~%"
_PCT = re.compile(r"%([0-9A-Fa-f]{2})")

MAX_URL_LENGTH = 2048


class InvalidURLError(ValueError):
    """Raised when a string cannot be accepted as a crawlable URL."""


def _normalize_percent_encoding(text: str) -> str:
    def fix(match: re.Match) -> str:
        char = chr(int(match.group(1), 16))
        return char if char in _UNRESERVED else f"%{match.group(1).upper()}"

    text = _PCT.sub(fix, text)
    # Encode anything that must not appear raw (spaces, non-ASCII...). '%' is
    # in the safe set so existing escapes are not double-encoded.
    return quote(text, safe=_PATH_SAFE)


def remove_dot_segments(path: str) -> str:
    """RFC 3986 §5.2.4 — resolve '.' and '..' in a path.

    "/a/b/c/./../../g" -> "/a/g"
    "/a/b/.."          -> "/a/"
    "/../x"            -> "/x"     (can't go above the root)
    """
    output: list[str] = []
    for segment in path.split("/"):
        if segment == "..":
            if len(output) > 1:  # output[0] is the empty string before the leading '/'
                output.pop()
        elif segment != ".":
            output.append(segment)
    result = "/".join(output)
    # A path that ended in '.' or '..' refers to a directory -> keep trailing slash.
    if path.endswith(("/.", "/..")) and not result.endswith("/"):
        result += "/"
    return result if result.startswith("/") else "/" + result


def _is_tracking(name: str) -> bool:
    lowered = name.lower()
    return lowered in TRACKING_PARAMS or lowered.startswith(TRACKING_PREFIXES)


def normalize_url(url: str) -> str:
    """Return the canonical form of an http(s) URL, or raise InvalidURLError."""
    if not isinstance(url, str):
        raise InvalidURLError("URL must be a string")
    url = url.strip()
    if not url:
        raise InvalidURLError("URL is empty")
    if len(url) > MAX_URL_LENGTH:
        raise InvalidURLError(f"URL longer than {MAX_URL_LENGTH} characters")

    try:
        parts = urlsplit(url)
        port = parts.port  # raises ValueError for e.g. ':abc' or ':99999'
    except ValueError as exc:
        raise InvalidURLError(f"Malformed URL: {exc}") from exc

    scheme = parts.scheme.lower()
    if not scheme:
        raise InvalidURLError("URL must start with http:// or https://")
    if scheme not in ALLOWED_SCHEMES:
        raise InvalidURLError(f"Unsupported scheme {parts.scheme!r}; only http and https are allowed")
    if parts.username is not None or parts.password is not None:
        raise InvalidURLError("URLs containing credentials are not allowed")

    host = (parts.hostname or "").rstrip(".")  # .hostname is already lower-cased
    if not host:
        raise InvalidURLError("URL has no host")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise InvalidURLError(f"Invalid host name {host!r}") from exc
    if ":" in host:  # IPv6 literal must stay in brackets
        host = f"[{host}]"

    netloc = host if port is None or port == DEFAULT_PORTS[scheme] else f"{host}:{port}"

    path = remove_dot_segments(_normalize_percent_encoding(parts.path or "/"))

    query_pairs = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)
    ]
    query_pairs.sort()  # by name, then value (stable for repeated names)
    query = urlencode(query_pairs, quote_via=quote, safe="-._~")

    return urlunsplit((scheme, netloc, path, query, ""))  # "" = drop fragment


def get_domain(url: str) -> str:
    """Host part of a URL, lower-cased, without port: 'https://Docs.Python.org:443/x' -> 'docs.python.org'."""
    host = urlsplit(normalize_url(url)).hostname
    assert host is not None  # normalize_url guarantees a host
    return host
