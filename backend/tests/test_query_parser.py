"""Phases 5 and 8: query parsing."""

import pytest

from app.search.query_parser import MAX_QUERY_LENGTH, And, Filter, Not, Or, Phrase, Term, describe, parse_query


@pytest.mark.parametrize(
    "query, mode, expected",
    [
        ("python programming", "basic", "OR(python, programming)"),
        ("python programming", "boolean", "AND(python, programming)"),
        ("python AND programming", "basic", "AND(python, programming)"),
        ("python && programming", "basic", "AND(python, programming)"),
        ("python OR javascript", "basic", "OR(python, javascript)"),
        ("python || javascript", "boolean", "OR(python, javascript)"),
        ("python NOT java", "basic", "AND(python, NOT(java))"),
        ("python -java", "basic", "AND(python, NOT(java))"),
        ('"machine learning"', "basic", '"machine learning"'),
        ("(python OR java) AND web", "basic", "AND(OR(python, java), web)"),
        ("a OR b AND c", "basic", "OR(a, AND(b, c))"),           # AND binds tighter than OR
        ("title:python", "basic", "title:python"),
        ('title:"neural networks"', "basic", 'title:"neural networks"'),
        ("python site:example.com", "basic", "AND(python, site:example.com)"),  # filters always AND
        ("author:girish", "basic", "author:girish"),
        ("machne~", "basic", "machne~"),
        ("machne lerning", "fuzzy", "OR(machne~, lerning~)"),
        ("machine learning", "phrase", '"machine learning"'),
        ("NOT java", "basic", "NOT(java)"),
        ("python and or", "basic", "OR(python, and, or)"),          # lowercase = ordinary words
        ("state-of-the-art", "basic", "state-of-the-art"),          # hyphen inside a word is not NOT
        ("c++ c#", "basic", "OR(c++, c#)"),
        ("TITLE:Python", "basic", "title:Python"),
    ],
)
def test_parse(query, mode, expected):
    assert describe(parse_query(query, mode).root) == expected


def test_ast_node_types():
    root = parse_query('python AND NOT "web app" site:x.com title:fast', "basic").root
    # AND binds tighter than the implicit OR; the site: filter is AND-ed with everything
    assert describe(root) == 'AND(OR(AND(python, NOT("web app")), title:fast), site:x.com)'
    assert isinstance(root, And) and isinstance(root.children[1], Filter)
    inner_and = root.children[0].children[0]
    assert inner_and.children == (Term("python"), Not(Phrase("web app")))
    assert isinstance(root.children[0], Or)


@pytest.mark.parametrize(
    "query, expected, warning",
    [
        ('"unclosed phrase', '"unclosed phrase"', "Unclosed quote"),
        ("(python OR java", "OR(python, java)", "Unclosed bracket"),
        ("python) java", "OR(python, java)", "Ignored unexpected"),
        ("python AND", "python", None),
        ("OR python", "python", None),
    ],
)
def test_malformed_queries_never_raise(query, expected, warning):
    parsed = parse_query(query)
    assert describe(parsed.root) == expected
    if warning:
        assert any(warning in w for w in parsed.warnings)


@pytest.mark.parametrize("query", ["", "   ", '""', "()", "NOT", "-", "...", "!!"])
def test_empty_or_meaningless(query):
    assert parse_query(query).root is None


def test_long_query_truncated():
    parsed = parse_query("word " * 200)
    assert any("truncated" in w for w in parsed.warnings)


def test_too_many_tokens_limited():
    parsed = parse_query(" ".join(f"w{i}" for i in range(100)))
    assert any("first" in w for w in parsed.warnings)
    assert len(parsed.root.children) == 64


def test_phrase_mode_strips_quotes():
    assert describe(parse_query('say "hello" world', "phrase").root) == '"say  hello  world"'


def test_max_length_constant():
    assert MAX_QUERY_LENGTH == 500
