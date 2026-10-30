"""
Token normalization: make different spellings of the same word identical.

    "Python", "PYTHON", "python"      -> "python"     (case folding)
    "café", "cafe"                     -> "cafe"       (accent folding)
    "ﬁle" (one 'fi' ligature char)     -> "file"       (Unicode NFKC)
    "python's"                         -> "python"     (possessive)
    "don't"                            -> "dont"       (apostrophes)
    "1,000,000"                        -> "1000000"    (thousands separators)

WHY
---
The index is an exact-match dictionary: "Python" and "python" would be two
different keys. Normalization maps every variant a user might type to one key,
so queries and documents meet in the middle. The SAME normalization must run on
documents (at index time) and on queries (at search time).

Normalization is applied to one token at a time (see analyzer.py), after
tokenization, so a token's start/end offsets still point into the original
text for highlighting.
"""

import re
import unicodedata

APOSTROPHES = "'’ʼ"  # ASCII ', right single quote ’, modifier letter apostrophe ʼ
_THOUSANDS = re.compile(r"^\d{1,3}(?:,\d{3})+$")


def _is_latin_base(ch: str) -> bool:
    """True for Latin letters: ASCII, Latin-1 Supplement, Latin Extended-A/B, Latin Extended Additional."""
    cp = ord(ch)
    return ch.isascii() and ch.isalpha() or 0x00C0 <= cp <= 0x024F or 0x1E00 <= cp <= 0x1EFF


def fold_accents(text: str) -> str:
    """Remove accents from LATIN letters only: 'café naïve' -> 'cafe naive'.

    How: NFKD splits 'é' into 'e' + U+0301 (combining acute). We then drop
    combining marks (category 'Mn') — but only when they sit on a Latin
    letter. In Kannada, Hindi, Tamil, Arabic etc. those marks are vowel signs
    and viramas that are part of the word's spelling; removing them would turn
    'ಕನ್ನಡ' into a different, meaningless string.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    out: list[str] = []
    base_is_latin = False
    for ch in decomposed:
        if unicodedata.category(ch) == "Mn":
            if base_is_latin:
                continue  # drop accent on a Latin letter
        else:
            base_is_latin = _is_latin_base(ch)
        out.append(ch)
    return unicodedata.normalize("NFC", "".join(out))


def normalize_token(token: str, *, fold: bool = True) -> str:
    """Normalize one token. Returns '' if nothing meaningful is left."""
    # 1. NFKC: compatibility forms -> standard forms (ligatures, full-width letters, superscripts)
    text = unicodedata.normalize("NFKC", token)
    # 2. casefold(): like lower() but complete ('ß' -> 'ss', 'ﬀ' -> 'ff')
    text = text.casefold()
    # 3. accents (Latin only)
    if fold:
        text = fold_accents(text)
    # 4. apostrophes: possessive "'s" dropped, others removed ("don't" -> "dont")
    for apos in APOSTROPHES:
        if text.endswith(apos + "s"):
            text = text[:-2]
        text = text.replace(apos, "")
    # 5. numbers: "1,000,000" -> "1000000" (only the unambiguous thousands pattern)
    if _THOUSANDS.match(text):
        text = text.replace(",", "")
    return text
