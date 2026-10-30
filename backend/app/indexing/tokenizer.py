"""
Tokenization: split text into words ("tokens"), remembering where each one is.

    "Learn C++ and Python 3.12, it's fun!"
     ->  Learn | C++ | and | Python | 3.12 | it's | fun

WHY WE DON'T USE  re.findall(r"\\w+", text)
-------------------------------------------
Python's \\w does not count Unicode combining marks as word characters, so it
breaks Indic words apart:   re.findall(r"\\w+", "ಕನ್ನಡ")  ->  ['ಕನ', 'ನಡ']
The vowel signs and viramas of Kannada, Hindi, Tamil... are combining marks.
Instead we scan character by character using Unicode categories:

    L* (letters), N* (numbers)  -> start or continue a word
    M* (combining marks)        -> continue a word (never start one)
    anything else               -> separator

RULES ON TOP OF THAT
--------------------
* Apostrophe between letters stays inside the token:  it's, don't, python's
* Digits joined by '.' or ',' stay together:          3.14, 1,000, 192.168.0.1, python3.12
* Programming-language names:                          c++, c#, f#
* Hyphens, underscores, slashes split:                 state-of-the-art -> state | of | the | art

Each token keeps its character offsets [start, end) into the ORIGINAL text,
so later phases can highlight matches in snippets without re-searching.
"""

import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass

from app.indexing.normalizer import APOSTROPHES


@dataclass(frozen=True, slots=True)
class RawToken:
    text: str   # exactly as it appears in the source
    start: int  # offset of first character
    end: int    # offset just past the last character (text == source[start:end])


def _cat(ch: str) -> str:
    return unicodedata.category(ch)[0]  # 'L', 'N', 'M', 'P', 'Z', 'S', 'C'


def _starts_word(ch: str) -> bool:
    return _cat(ch) in ("L", "N")


def _continues_word(ch: str) -> bool:
    return _cat(ch) in ("L", "N", "M")


def tokenize(text: str) -> Iterator[RawToken]:
    """Yield tokens with their offsets. Pure function, no normalization."""
    n = len(text)
    i = 0
    while i < n:
        if not _starts_word(text[i]):
            i += 1
            continue

        start = i
        i += 1
        while i < n:
            ch = text[i]
            if _continues_word(ch):
                i += 1
            elif ch in APOSTROPHES and i + 1 < n and _cat(text[i + 1]) == "L" and _cat(text[i - 1]) == "L":
                i += 2  # it's, don't
            elif ch in ".," and i + 1 < n and text[i + 1].isdigit() and text[i - 1].isdigit():
                i += 2  # 3.14, 1,000
            else:
                break

        # c++ / c# / f# : a single ASCII letter followed by '++' or '#', then a non-word char
        if i - start == 1 and text[start].isascii() and text[start].isalpha():
            for suffix in ("++", "#"):
                after = i + len(suffix)
                if text.startswith(suffix, i) and (after >= n or not _continues_word(text[after])):
                    i = after
                    break

        yield RawToken(text[start:i], start, i)
