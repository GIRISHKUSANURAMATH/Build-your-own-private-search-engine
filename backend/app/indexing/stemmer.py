"""
The Porter stemming algorithm, implemented from the original paper:

    M.F. Porter, "An algorithm for suffix stripping", Program 14(3), 1980.

WHAT STEMMING IS
----------------
Stemming chops suffixes off words so that different forms of the same word
become one index term:

    connect, connected, connecting, connection, connections  ->  connect

A stem does NOT have to be a real word ("happy" -> "happi", "language" ->
"languag"). That is fine: stems are only ever compared with other stems,
because documents AND queries go through the same stemmer.

THE IDEA BEHIND PORTER
----------------------
Every word can be written as   [C](VC){m}[V]
where C is a run of consonants, V a run of vowels, and m is the "measure"
(roughly: number of syllables in the stem).

    m=0: tr, ee, tree, by          m=1: trouble, oats, trees, ivy
    m=2: troubles, private, oaten

Rules look like   (condition) SUFFIX -> REPLACEMENT   e.g.  (m>0) ational -> ate
and the conditions stop the algorithm from mangling short words:
"relational" -> "relate" (stem 'rel' has m=1), but "rational" keeps its
ending in step 2 because 'r' has m=0.

Five steps run in order:
  1  plurals and -ed / -ing          (caresses->caress, motoring->motor)
  2  double suffixes -> single        (relational->relate, digitizer->digitize)
  3  -ic-, -full, -ness ...           (hopeful->hope, goodness->good)
  4  remove -ant, -ence, -ment ...    (revival->reviv, adjustment->adjust)
  5  tidy up: final -e, double -ll    (probate->probat, controll->control)

KNOWN WEAKNESSES (good viva material)
-------------------------------------
* Over-stemming:  university, universe, universal  -> univers  (unrelated words merge)
* Under-stemming: alumnus/alumni, absorb/absorption stay different
* English only.  Words in other scripts pass through unchanged.

This implementation follows the paper exactly (the same as NLTK's
PorterStemmer(mode=ORIGINAL_ALGORITHM)); tests/data/porter_vocabulary.tsv
holds thousands of word -> stem pairs checked against that reference.
"""

from functools import lru_cache

_VOWELS = frozenset("aeiou")


class PorterStemmer:
    # ------------------------------------------------------------ letter classes
    @staticmethod
    def _is_consonant(word: str, i: int) -> bool:
        """a,e,i,o,u are vowels. 'y' is a consonant at the start or after a vowel,
        and a vowel after a consonant ("toy": y is consonant; "syzygy": vowels)."""
        ch = word[i]
        if ch in _VOWELS:
            return False
        if ch == "y":
            return i == 0 or not PorterStemmer._is_consonant(word, i - 1)
        return True

    @classmethod
    def measure(cls, stem: str) -> int:
        """m in [C](VC){m}[V]: count vowel-run -> consonant-run transitions."""
        m = 0
        prev_vowel = False
        for i in range(len(stem)):
            consonant = cls._is_consonant(stem, i)
            if consonant and prev_vowel:
                m += 1
            prev_vowel = not consonant
        return m

    @classmethod
    def _contains_vowel(cls, stem: str) -> bool:  # condition *v*
        return any(not cls._is_consonant(stem, i) for i in range(len(stem)))

    @classmethod
    def _ends_double_consonant(cls, word: str) -> bool:  # condition *d
        return len(word) >= 2 and word[-1] == word[-2] and cls._is_consonant(word, len(word) - 1)

    @classmethod
    def _ends_cvc(cls, word: str) -> bool:
        """Condition *o: ends consonant-vowel-consonant, last one not w, x or y
        (e.g. -WIL, -HOP), signalling a short syllable: hop(e), fil(e)."""
        return (
            len(word) >= 3
            and cls._is_consonant(word, len(word) - 3)
            and not cls._is_consonant(word, len(word) - 2)
            and cls._is_consonant(word, len(word) - 1)
            and word[-1] not in "wxy"
        )

    # ------------------------------------------------------------ rule helper
    @staticmethod
    def _apply_longest(word: str, rules: list[tuple[str, str]], condition) -> str:
        """Find the LONGEST matching suffix; apply it only if condition(stem) holds.

        Per the paper, if the longest match fails its condition, no shorter
        suffix is tried (e.g. step 4 on "...ement" never falls back to "-ent").
        """
        for suffix, replacement in rules:  # rule lists are sorted longest-first
            if word.endswith(suffix):
                stem = word[: len(word) - len(suffix)]
                return stem + replacement if condition(stem) else word
        return word

    # ------------------------------------------------------------ steps
    def _step1a(self, w: str) -> str:
        if w.endswith("sses"):
            return w[:-2]          # caresses -> caress
        if w.endswith("ies"):
            return w[:-2]          # ponies -> poni
        if w.endswith("ss"):
            return w               # caress -> caress
        if w.endswith("s"):
            return w[:-1]          # cats -> cat
        return w

    def _step1b(self, w: str) -> str:
        if w.endswith("eed"):
            stem = w[:-3]
            return w[:-1] if self.measure(stem) > 0 else w   # agreed -> agree, feed -> feed
        for suffix in ("ed", "ing"):
            if w.endswith(suffix):
                stem = w[: -len(suffix)]
                if self._contains_vowel(stem):               # plastered -> plaster, bled stays
                    return self._step1b_cleanup(stem)
                return w
        return w

    def _step1b_cleanup(self, w: str) -> str:
        """After removing -ed/-ing, repair the stem so related forms line up."""
        if w.endswith(("at", "bl", "iz")):
            return w + "e"                                   # conflat(ed) -> conflate
        if self._ends_double_consonant(w) and w[-1] not in "lsz":
            return w[:-1]                                    # hopp(ing) -> hop, but fall(ing) -> fall
        if self.measure(w) == 1 and self._ends_cvc(w):
            return w + "e"                                   # fil(ing) -> file
        return w

    def _step1c(self, w: str) -> str:
        if w.endswith("y") and self._contains_vowel(w[:-1]):
            return w[:-1] + "i"                              # happy -> happi, sky stays
        return w

    _STEP2 = sorted(
        [
            ("ational", "ate"), ("tional", "tion"), ("enci", "ence"), ("anci", "ance"),
            ("izer", "ize"), ("abli", "able"), ("alli", "al"), ("entli", "ent"),
            ("eli", "e"), ("ousli", "ous"), ("ization", "ize"), ("ation", "ate"),
            ("ator", "ate"), ("alism", "al"), ("iveness", "ive"), ("fulness", "ful"),
            ("ousness", "ous"), ("aliti", "al"), ("iviti", "ive"), ("biliti", "ble"),
        ],
        key=lambda r: -len(r[0]),
    )

    _STEP3 = sorted(
        [
            ("icate", "ic"), ("ative", ""), ("alize", "al"), ("iciti", "ic"),
            ("ical", "ic"), ("ful", ""), ("ness", ""),
        ],
        key=lambda r: -len(r[0]),
    )

    _STEP4 = sorted(
        [
            ("al", ""), ("ance", ""), ("ence", ""), ("er", ""), ("ic", ""), ("able", ""),
            ("ible", ""), ("ant", ""), ("ement", ""), ("ment", ""), ("ent", ""), ("ion", ""),
            ("ou", ""), ("ism", ""), ("ate", ""), ("iti", ""), ("ous", ""), ("ive", ""), ("ize", ""),
        ],
        key=lambda r: -len(r[0]),
    )

    def _step2(self, w: str) -> str:
        return self._apply_longest(w, self._STEP2, lambda s: self.measure(s) > 0)

    def _step3(self, w: str) -> str:
        return self._apply_longest(w, self._STEP3, lambda s: self.measure(s) > 0)

    def _step4(self, w: str) -> str:
        def condition(stem: str) -> bool:
            return self.measure(stem) > 1

        for suffix, _ in self._STEP4:
            if w.endswith(suffix):
                stem = w[: -len(suffix)]
                if suffix == "ion":  # (m>1 and (*S or *T)) ION ->     adoption -> adopt
                    ok = condition(stem) and stem.endswith(("s", "t"))
                else:
                    ok = condition(stem)
                return stem if ok else w
        return w

    def _step5a(self, w: str) -> str:
        if w.endswith("e"):
            stem = w[:-1]
            m = self.measure(stem)
            if m > 1 or (m == 1 and not self._ends_cvc(stem)):
                return stem                                  # probate -> probat, rate stays
        return w

    def _step5b(self, w: str) -> str:
        if self.measure(w) > 1 and self._ends_double_consonant(w) and w.endswith("l"):
            return w[:-1]                                    # controll -> control
        return w

    # ------------------------------------------------------------ public API
    def stem(self, word: str) -> str:
        """Stem one lower-case word. Non-ASCII-letter words are returned unchanged."""
        if len(word) <= 2 or not word.isascii() or not word.isalpha():
            # Porter's rules only make sense for English letters; numbers,
            # 'c++', and words in other scripts pass through untouched.
            return word
        w = self._step1a(word)
        w = self._step1b(w)
        w = self._step1c(w)
        w = self._step2(w)
        w = self._step3(w)
        w = self._step4(w)
        w = self._step5a(w)
        w = self._step5b(w)
        return w


_DEFAULT = PorterStemmer()


@lru_cache(maxsize=100_000)
def stem(word: str) -> str:
    """Cached module-level helper: real text repeats words constantly, so
    caching makes stemming a large corpus several times faster."""
    return _DEFAULT.stem(word)
