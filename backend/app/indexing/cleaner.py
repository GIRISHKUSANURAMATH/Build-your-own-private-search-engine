"""
Text cleaning: make extracted text tidy WITHOUT changing its words.

Cleaning is different from normalization:
* cleaning keeps case and punctuation — the cleaned text is what gets stored
  in documents.content and shown in snippets, so it must still read naturally;
* normalization (normalizer.py) is per-token and only feeds the index.

Steps:
1. Unify line endings (\\r\\n, \\r -> \\n).
2. Delete invisible characters: control characters, zero-width spaces/joiners,
   byte-order marks, soft hyphens. They break word matching ("pyth\\u200bon"
   would otherwise be two tokens) and are invisible to readers anyway.
3. Turn every kind of space (non-breaking, em-space, tabs...) into ' '.
4. Collapse runs of spaces inside each line; trim each line.
5. Keep at most one blank line between paragraphs.
"""

import re
import unicodedata

# Zero-width space/non-joiner/joiner, word joiner, BOM, soft hyphen.
# NOTE: U+200C/U+200D (ZWNJ/ZWJ) affect rendering in some Indic scripts, but
# not meaning for search; removing them makes matching more consistent.
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"))
_SPACES = re.compile(r"[^\S\n]+")      # any whitespace except newline
_MANY_NEWLINES = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.translate(_INVISIBLE)
    # Remove remaining control characters (category Cc) except newline and tab.
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch) != "Cc")
    lines = (_SPACES.sub(" ", line).strip() for line in text.split("\n"))
    text = "\n".join(lines)
    return _MANY_NEWLINES.sub("\n\n", text).strip()
