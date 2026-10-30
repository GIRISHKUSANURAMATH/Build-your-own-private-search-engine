"""Phase 3: Porter stemmer."""

from pathlib import Path

import pytest

from app.indexing.stemmer import PorterStemmer, stem

VOCAB = Path(__file__).parent / "data" / "porter_vocabulary.tsv"
P = PorterStemmer()


def _pairs():
    for line in VOCAB.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            word, expected = line.split("\t")
            yield word, expected


def test_reference_vocabulary_matches_exactly():
    """4,000+ words checked against NLTK's PorterStemmer(mode=ORIGINAL_ALGORITHM)."""
    pairs = list(_pairs())
    assert len(pairs) > 4000
    mismatches = [(w, exp, P.stem(w)) for w, exp in pairs if P.stem(w) != exp]
    assert mismatches == [], mismatches[:20]


# Examples straight from Porter's 1980 paper, step by step.
@pytest.mark.parametrize(
    "word, expected",
    [
        # step 1a
        ("caresses", "caress"), ("ponies", "poni"), ("ties", "ti"), ("caress", "caress"), ("cats", "cat"),
        # step 1b
        ("feed", "feed"), ("agreed", "agre"), ("plastered", "plaster"), ("bled", "bled"),
        ("motoring", "motor"), ("sing", "sing"),
        # step 1b cleanup
        ("conflated", "conflat"), ("troubled", "troubl"), ("sized", "size"), ("hopping", "hop"),
        ("tanned", "tan"), ("falling", "fall"), ("hissing", "hiss"), ("fizzed", "fizz"),
        ("failing", "fail"), ("filing", "file"),
        # step 1c
        ("happy", "happi"), ("sky", "sky"),
        # steps 2-5 on full words
        ("relational", "relat"), ("conditional", "condit"), ("rational", "ration"),
        ("hopeful", "hope"), ("goodness", "good"), ("revival", "reviv"), ("adjustment", "adjust"),
        ("adoption", "adopt"), ("probate", "probat"), ("rate", "rate"), ("controll", "control"), ("roll", "roll"),
        ("generalizations", "gener"), ("oscillators", "oscil"),
    ],
)
def test_paper_examples(word, expected):
    assert P.stem(word) == expected


def test_word_family_collapses_to_one_term():
    family = ["connect", "connected", "connecting", "connection", "connections"]
    assert {P.stem(w) for w in family} == {"connect"}


def test_known_overstemming_is_documented_behaviour():
    # Unrelated words merge. This is a property of Porter, not a bug in our code.
    assert P.stem("university") == P.stem("universe") == P.stem("universal") == "univers"


@pytest.mark.parametrize(
    "stem_, m",
    [("tr", 0), ("ee", 0), ("tree", 0), ("y", 0), ("by", 0),
     ("trouble", 1), ("oats", 1), ("trees", 1), ("ivy", 1),
     ("troubles", 2), ("private", 2), ("oaten", 2), ("orrery", 2)],
)
def test_measure_matches_paper_table(stem_, m):
    assert PorterStemmer.measure(stem_) == m


def test_y_is_consonant_or_vowel_by_position():
    assert PorterStemmer._is_consonant("toy", 2) is True    # after a vowel -> consonant
    assert PorterStemmer._is_consonant("syzygy", 1) is False  # after a consonant -> vowel
    assert PorterStemmer._is_consonant("yes", 0) is True    # first letter -> consonant


@pytest.mark.parametrize("word", ["a", "is", "as", "ox", "s"])
def test_words_of_length_two_or_less_are_unchanged(word):
    # Same as Porter's own reference implementation (NLTK's paper-exact mode
    # would turn "as" into "a" and "s" into "").
    assert P.stem(word) == word


@pytest.mark.parametrize("word", ["ಕನ್ನಡ", "हिन्दी", "3.14", "c++", "python3", "naïve"])
def test_non_english_or_non_alphabetic_words_pass_through(word):
    assert P.stem(word) == word


def test_cached_helper_matches_class():
    assert stem("connections") == P.stem("connections") == "connect"
