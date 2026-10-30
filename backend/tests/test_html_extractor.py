"""Phase 3: HTML extraction."""

import pytest

from app.indexing.html_extractor import UnsupportedContentType, extract_document, extract_html, extract_text

PAGE = """<!doctype html>
<html lang="en-US">
<head>
  <meta charset="utf-8">
  <title>  Python Tutorial –
     Example </title>
  <meta name="description" content="Learn Python step by step">
  <meta name="author" content="Girish">
  <link rel="canonical" href="https://example.com/python">
  <style>body { color: red }</style>
  <script>var secret = "SCRIPT_TEXT";</script>
</head>
<body>
  <header class="site-header"><a href="/">Home</a> <a href="/about">About</a></header>
  <nav><ul><li>Menu item</li></ul></nav>
  <div class="cookie-banner">We use cookies</div>
  <main>
    <article>
      <header><h1>Learn Python</h1><p>by Girish</p></header>
      <p>Python is a <b>powerful</b> programming&nbsp;language &amp; easy to read.</p>
      <!-- an HTML comment -->
      <p style="display: none">INVISIBLE_STYLE</p>
      <span hidden>INVISIBLE_ATTR</span>
      <div aria-hidden="true">INVISIBLE_ARIA</div>
      <h2>Why Python?</h2>
      <ul><li>Easy</li><li>Readable</li></ul>
      <form><label>Search</label><input value="q"><button>Go</button></form>
      <aside>Related links</aside>
      <footer>Article footer note</footer>
    </article>
  </main>
  <footer>© 2026 Example Inc</footer>
</body>
</html>"""


@pytest.fixture(scope="module")
def doc():
    return extract_html(PAGE)


def test_metadata(doc):
    assert doc.title == "Python Tutorial – Example"
    assert doc.description == "Learn Python step by step"
    assert doc.author == "Girish"
    assert doc.language == "en-us"
    assert doc.canonical_url == "https://example.com/python"
    assert doc.truncated is False


def test_main_content_is_extracted(doc):
    assert "Python is a powerful programming language & easy to read." in doc.text  # entities decoded
    assert "Learn Python" in doc.text            # article's own header is content
    assert "Article footer note" in doc.text     # article's own footer is content
    assert "Easy\nReadable" in doc.text          # list items on separate lines


@pytest.mark.parametrize(
    "noise",
    ["SCRIPT_TEXT", "color: red", "Menu item", "We use cookies", "Home", "© 2026",
     "INVISIBLE_STYLE", "INVISIBLE_ATTR", "INVISIBLE_ARIA", "an HTML comment",
     "Related links", "Search", "Go"],
)
def test_noise_is_removed(doc, noise):
    assert noise not in doc.text


def test_headings_collected(doc):
    assert doc.headings == ["Learn Python", "Why Python?"]


def test_paragraphs_become_lines():
    d = extract_html("<body><p>One</p><p>Two<br>Three</p><div>Four</div></body>")
    assert d.text.split("\n") == ["One", "Two", "Three", "Four"]


def test_inline_tags_do_not_split_words():
    d = extract_html("<p>Py<b>thon</b> is <i>fun</i></p>")
    assert d.text == "Python is fun"


def test_table_cells_are_separated():
    d = extract_html("<table><tr><td>Name</td><td>Python</td></tr><tr><td>Year</td><td>1991</td></tr></table>")
    assert d.text == "Name Python\nYear 1991"


def test_largest_article_wins_without_main():
    html = ("<body><article><p>short teaser</p></article>"
            "<article><p>" + "The real article body. " * 10 + "</p></article></body>")
    d = extract_html(html)
    assert "The real article body." in d.text and "short teaser" not in d.text


def test_falls_back_to_body_when_main_is_nearly_empty():
    html = "<body><main><p>Hi</p></main><div>" + "Actual content lives outside main. " * 5 + "</div></body>"
    assert "Actual content lives outside main." in extract_html(html).text


def test_page_without_body_or_html_tags():
    d = extract_html("Just some <b>text</b> without structure")
    assert d.text == "Just some text without structure"
    assert d.title == ""


def test_title_fallbacks():
    assert extract_html('<head><meta property="og:title" content="OG Title"></head><p>x</p>').title == "OG Title"
    assert extract_html("<body><h1>Heading Title</h1><p>text</p></body>").title == "Heading Title"


def test_language_from_meta_http_equiv():
    d = extract_html('<head><meta http-equiv="Content-Language" content="kn"></head><p>ಕನ್ನಡ</p>')
    assert d.language == "kn" and d.text == "ಕನ್ನಡ"


def test_boilerplate_class_matching_is_exact_not_substring():
    html = '<body><div class="comments-on-design">Keep me</div><div class="sidebar">Drop me</div></body>'
    d = extract_html(html)
    assert "Keep me" in d.text and "Drop me" not in d.text


def test_aria_landmark_roles_removed():
    html = ('<body><div role="navigation">NAV</div><div role="banner">BANNER</div>'
            '<div role="main"><p>Main text here</p></div><div role="contentinfo">INFO</div></body>')
    d = extract_html(html)
    assert d.text == "Main text here"


def test_malformed_html_does_not_crash():
    d = extract_html("<html><body><p>Unclosed <b>bold <i>italic</p><div>after</body>")
    assert "Unclosed bold italic" in d.text and "after" in d.text


def test_bytes_input_with_declared_charset():
    html = '<html><head><meta charset="windows-1252"></head><body><p>caf\xe9 \x93quoted\x94</p></body></html>'
    d = extract_html(html.encode("latin-1"))
    assert d.text == "café “quoted”"


def test_bytes_input_utf8():
    d = extract_html("<p>ಕನ್ನಡ café</p>".encode("utf-8"))
    assert d.text == "ಕನ್ನಡ café"


def test_oversized_input_is_truncated():
    html = "<p>" + "word " * 1000 + "</p>"
    d = extract_html(html, max_bytes=100)
    assert d.truncated is True
    assert len(d.text) < 100


def test_empty_input():
    d = extract_html("")
    assert d.text == "" and d.title == ""


# ---------------------------------------------------------------- dispatch / plain text
def test_extract_text_plain():
    d = extract_text("\n\n  My Notes  \nLine two\r\nLine three  ")
    assert d.title == "My Notes"
    assert d.text == "My Notes\nLine two\nLine three"


def test_extract_text_bytes_with_invalid_utf8():
    d = extract_text(b"ok \xff\xfe end")
    assert d.text.startswith("ok") and d.text.endswith("end")


@pytest.mark.parametrize("ctype", ["text/html", "text/html; charset=utf-8", "TEXT/HTML", "application/xhtml+xml"])
def test_dispatch_html(ctype):
    assert extract_document("<p>hi</p>", ctype).text == "hi"


def test_dispatch_plain():
    assert extract_document("hi <b>not html</b>", "text/plain").text == "hi <b>not html</b>"


@pytest.mark.parametrize("ctype", ["application/pdf", "image/png", "application/octet-stream"])
def test_dispatch_rejects_unsupported(ctype):
    with pytest.raises(UnsupportedContentType):
        extract_document(b"%PDF-1.7", ctype)
