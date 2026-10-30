"""Phase 2: URL normalization."""

import pytest

from app.utils.urls import InvalidURLError, get_domain, normalize_url, remove_dot_segments


@pytest.mark.parametrize(
    "raw, expected",
    [
        # scheme + host lower-cased, default port removed, empty path -> "/"
        ("HTTP://Example.COM", "http://example.com/"),
        ("http://example.com:80/", "http://example.com/"),
        ("https://example.com:443/a", "https://example.com/a"),
        ("https://example.com:8443/a", "https://example.com:8443/a"),  # non-default port kept
        # fragment removed
        ("https://example.com/page#section-2", "https://example.com/page"),
        # dot segments resolved
        ("https://example.com/a/./b/../c", "https://example.com/a/c"),
        ("https://example.com/a/b/..", "https://example.com/a/"),
        ("https://example.com/../../x", "https://example.com/x"),
        # query sorted, tracking params removed, blank values kept
        ("https://example.com/s?b=2&a=1", "https://example.com/s?a=1&b=2"),
        ("https://example.com/s?q=py&utm_source=tw&UTM_Medium=x&gclid=1&fbclid=2", "https://example.com/s?q=py"),
        ("https://example.com/s?empty=&a=1", "https://example.com/s?a=1&empty="),
        ("https://example.com/s?utm_source=only", "https://example.com/s"),
        # percent-encoding: unreserved decoded, hex upper-cased, reserved %2F kept
        ("https://example.com/%7euser", "https://example.com/~user"),
        ("https://example.com/a%2fb", "https://example.com/a%2Fb"),
        ("https://example.com/caf%c3%a9", "https://example.com/caf%C3%A9"),
        ("https://example.com/a b", "https://example.com/a%20b"),
        # trailing dot on host, IDN host -> punycode
        ("https://example.com./x", "https://example.com/x"),
        ("https://münchen.de/", "https://xn--mnchen-3ya.de/"),
        # whitespace around the URL
        ("   https://example.com/x  ", "https://example.com/x"),
        # IPv6 literal
        ("http://[::1]:80/x", "http://[::1]/x"),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


@pytest.mark.parametrize(
    "a, b",
    [
        ("https://example.com/Docs", "https://example.com/docs"),     # path is case-sensitive
        ("https://example.com/docs", "https://example.com/docs/"),    # trailing slash can matter
        ("https://www.example.com/", "https://example.com/"),         # www is a different host
        ("http://example.com/", "https://example.com/"),              # scheme differs
    ],
)
def test_unsafe_rewrites_are_not_applied(a, b):
    assert normalize_url(a) != normalize_url(b)


@pytest.mark.parametrize(
    "url",
    [
        "https://Example.com:443/a/./b/../c?z=1&a=2#x",
        "http://münchen.de/caf%c3%a9?q=a b",
        "https://example.com/a%2fb/%7e",
    ],
)
def test_normalization_is_idempotent(url):
    once = normalize_url(url)
    assert normalize_url(once) == once


@pytest.mark.parametrize(
    "bad",
    [
        "", "   ", "example.com", "ftp://example.com/file", "javascript:alert(1)",
        "file:///etc/passwd", "data:text/html,hi", "http://", "http://user:pass@example.com/",
        "http://example.com:abc/", "http://example.com:99999/", "https://" + "a" * 2050 + ".com",
    ],
)
def test_invalid_urls_are_rejected(bad):
    with pytest.raises(InvalidURLError):
        normalize_url(bad)


def test_non_string_rejected():
    with pytest.raises(InvalidURLError):
        normalize_url(None)  # type: ignore[arg-type]


def test_get_domain():
    assert get_domain("https://Docs.Python.org:443/3/") == "docs.python.org"
    assert get_domain("http://example.com:8080/x") == "example.com"


@pytest.mark.parametrize(
    "path, expected",
    [("/a/b/c/./../../g", "/a/g"), ("/a/b/..", "/a/"), ("/./", "/"), ("/a//b", "/a//b"), ("/..", "/")],
)
def test_remove_dot_segments(path, expected):
    assert remove_dot_segments(path) == expected
