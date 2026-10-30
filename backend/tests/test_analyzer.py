"""Phase 3: the full Analyzer pipeline."""

from app.indexing.analyzer import DEFAULT_ANALYZER, Analyzer, analyze


def test_roadmap_example():
    text = "Python is a powerful programming language."
    assert [t.normalized for t in analyze(text)] == ["python", "powerful", "programming", "language"]
    assert DEFAULT_ANALYZER.terms(text) == ["python", "power", "program", "languag"]


def test_positions_keep_gaps_for_removed_stopwords():
    tokens = analyze("university of london")
    assert [(t.term, t.position) for t in tokens] == [("univers", 0), ("london", 2)]


def test_token_offsets_point_into_text():
    text = "The  QUICK brown fox's jumps over C++ — naïve résumés."
    for t in analyze(text):
        assert text[t.start:t.end] == t.original


def test_document_and_query_meet_after_analysis():
    doc = DEFAULT_ANALYZER.terms("Connecting to databases: connection pooling explained")
    query = DEFAULT_ANALYZER.terms("database connections")
    assert set(query) <= set(doc)


def test_case_accent_and_ligature_variants_produce_same_terms():
    variants = ["Café résumé", "CAFE RESUME", "cafe resume", "Café résumé"]
    results = {tuple(DEFAULT_ANALYZER.terms(v)) for v in variants}
    assert len(results) == 1


def test_normalized_field_keeps_real_words_for_fuzzy_matching():
    (token,) = analyze("Machines")
    assert token.term == "machin"           # stem, used in the index
    assert token.normalized == "machines"   # real word, used for fuzzy suggestions
    assert token.original == "Machines"


def test_stopword_removal_can_be_disabled():
    a = Analyzer(remove_stopwords=False)
    assert a.terms("to be or not to be") == ["to", "be", "or", "not", "to", "be"]
    assert DEFAULT_ANALYZER.terms("to be or not to be") == []


def test_stemming_can_be_disabled():
    assert Analyzer(stem=False).terms("running programs") == ["running", "programs"]


def test_lowercasing_can_be_disabled():
    a = Analyzer(lowercase_and_fold=False, remove_stopwords=False, stem=False)
    assert a.terms("Python IS Great") == ["Python", "IS", "Great"]


def test_trace_explains_every_drop():
    long_word = "x" * 65
    traces = DEFAULT_ANALYZER.trace(f"The {long_word} parser's output")
    reasons = [(t.original[:5], t.dropped) for t in traces]
    assert reasons == [("The", "stopword"), ("xxxxx", "too_long"), ("parse", None), ("outpu", None)]
    assert [t.position for t in traces] == [0, 1, 2, 3]
    assert traces[2].term == "parser"


def test_custom_stopwords():
    a = Analyzer(stopwords=frozenset({"python"}))
    assert a.terms("python and java") == ["and", "java"]


def test_programming_terms_survive_pipeline():
    assert DEFAULT_ANALYZER.terms("C++ vs C# vs Python 3.12") == ["c++", "vs", "c#", "vs", "python", "3.12"]


def test_mixed_language_text():
    terms = DEFAULT_ANALYZER.terms("Learning ಕನ್ನಡ and हिन्दी")
    assert terms == ["learn", "ಕನ್ನಡ", "हिन्दी"]


def test_empty_and_punctuation_only_input():
    assert analyze("") == []
    assert analyze("... --- !!!") == []


def test_config_reports_settings():
    cfg = Analyzer(stem=False).config()
    assert cfg["stem"] is False and cfg["remove_stopwords"] is True and cfg["stopword_count"] > 100
