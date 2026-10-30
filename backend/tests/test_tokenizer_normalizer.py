"""Phase 3: tokenization, normalization, cleaning, stop words."""

import pytest

from app.indexing.cleaner import clean_text
from app.indexing.normalizer import fold_accents, normalize_token
from app.indexing.stopwords import STOPWORDS, is_stopword
from app.indexing.tokenizer import tokenize


def toks(text):
    return [t.text for t in tokenize(text)]


# ---------------------------------------------------------------- tokenizer
@pytest.mark.parametrize(
    "text, expected",
    [
        ("Python is a powerful programming language.", ["Python", "is", "a", "powerful", "programming", "language"]),
        ("hello,world!how are   you?", ["hello", "world", "how", "are", "you"]),
        ("state-of-the-art", ["state", "of", "the", "art"]),
        ("snake_case and/or path\\to", ["snake", "case", "and", "or", "path", "to"]),
        ("it's don't Python's", ["it's", "don't", "Python's"]),
        ("don’t", ["don’t"]),                            # typographic apostrophe
        ("'quoted' words", ["quoted", "words"]),         # quotes are not apostrophes inside words
        ("pi is 3.14 and 1,000 users at 192.168.0.1", ["pi", "is", "3.14", "and", "1,000", "users", "at", "192.168.0.1"]),
        ("end. Next, 2.", ["end", "Next", "2"]),        # sentence punctuation still splits
        ("python3.12 html5", ["python3.12", "html5"]),
        ("C++ C# F# are languages", ["C++", "C#", "F#", "are", "languages"]),
        ("abc++ c++11 #tag", ["abc", "c", "11", "tag"]),
        ("", []),
        ("   ...!!! ---", []),
    ],
)
def test_tokenize(text, expected):
    assert toks(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("ಕನ್ನಡ ಭಾಷೆ", ["ಕನ್ನಡ", "ಭಾಷೆ"]),     # Kannada: vowel signs + virama stay inside words
        ("हिन्दी भाषा", ["हिन्दी", "भाषा"]),     # Hindi
        ("தமிழ்", ["தமிழ்"]),                 # Tamil
        ("日本語", ["日本語"]),                  # CJK: no spaces, one token (see known limitations)
        ("Ünïcödé café", ["Ünïcödé", "café"]),
    ],
)
def test_tokenize_non_latin_scripts_keeps_words_whole(text, expected):
    assert toks(text) == expected


def test_offsets_point_into_original_text():
    text = "  Learn C++ & Python's 3.14 features — ಕನ್ನಡ!  "
    for t in tokenize(text):
        assert text[t.start:t.end] == t.text


# ---------------------------------------------------------------- normalizer
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Python", "python"), ("PYTHON", "python"),
        ("Café", "cafe"), ("naïve", "naive"), ("Résumé", "resume"),
        ("Straße", "strasse"),          # casefold, not lower()
        ("ﬁle", "file"),                # ligature via NFKC
        ("Ｐｙｔｈｏｎ", "python"),        # full-width letters via NFKC
        ("Python's", "python"), ("python’s", "python"), ("don't", "dont"),
        ("1,000,000", "1000000"), ("1,5", "1,5"), ("3.14", "3.14"),
        ("C++", "c++"), ("ಕನ್ನಡ", "ಕನ್ನಡ"),
    ],
)
def test_normalize_token(raw, expected):
    assert normalize_token(raw) == expected


def test_accent_folding_can_be_disabled():
    assert normalize_token("Café", fold=False) == "café"


@pytest.mark.parametrize("word", ["ಕನ್ನಡ", "हिन्दी", "தமிழ்", "العربية"])
def test_fold_accents_never_damages_non_latin_scripts(word):
    # Their combining marks are part of the spelling, not accents.
    assert fold_accents(word) == word


def test_fold_accents_latin():
    assert fold_accents("àéîõü ñ ç Ångström") == "aeiou n c Angstrom"


# ---------------------------------------------------------------- cleaner
def test_clean_text_whitespace_and_paragraphs():
    raw = "  Hello  world \t !\r\n\r\n\r\n\r\nNew   paragraph  \n line two "
    assert clean_text(raw) == "Hello world !\n\nNew paragraph\nline two"


def test_clean_text_removes_invisible_characters():
    assert clean_text("pyth​on­ is ﻿fun\x00\x07") == "python is fun"


def test_clean_text_keeps_case_and_punctuation():
    assert clean_text("Learn C++, Python & SQL!") == "Learn C++, Python & SQL!"


@pytest.mark.parametrize("empty", ["", "   ", "\n\n\t"])
def test_clean_text_empty(empty):
    assert clean_text(empty) == ""


# ---------------------------------------------------------------- stop words
def test_common_function_words_are_stopwords():
    for w in ["the", "is", "a", "of", "and", "to", "in", "dont", "its"]:
        assert is_stopword(w)


def test_content_words_are_not_stopwords():
    for w in ["python", "data", "computer", "learning", "search", "network", "c"]:
        assert not is_stopword(w)


def test_stopwords_are_normalized_form():
    # Every entry must be what normalize_token() would produce, or it could never match.
    assert all(normalize_token(w) == w for w in STOPWORDS)
