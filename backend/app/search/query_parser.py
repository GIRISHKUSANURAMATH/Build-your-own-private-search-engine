"""
Query parser: turns the text a user types into a tree (AST) the engine can evaluate.

SYNTAX
------
    python programming          plain words (combined with the default operator)
    python AND django           both required        (operators must be UPPERCASE;
    python OR java              either one             lowercase "and"/"or" are words)
    python NOT java             exclude              ( "-java" is a shortcut )
    "machine learning"          exact phrase (word positions must be adjacent)
    (python OR java) AND web    grouping
    title:python                term must be in the title
    title:"neural networks"     phrase in the title
    site:example.com            only documents from that domain (or its subdomains)
    author:girish               only documents whose author matches
    machne~                     fuzzy: also match words within a small edit distance

GRAMMAR (lowest to highest precedence)
--------------------------------------
    query    := or_expr
    or_expr  := seq ( "OR" seq )*
    seq      := and_expr+                 combined with the DEFAULT operator
    and_expr := unary ( "AND" unary )*
    unary    := ("NOT" | "-") unary | atom
    atom     := "(" or_expr ")" | PHRASE | FIELD ":" (WORD | PHRASE) | WORD ["~"]

The default operator is OR for "basic" mode (ranked retrieval: documents that
match more terms score higher) and AND for "boolean" mode (all terms required).
Inside a sequence, NOT items and filters (site:, author:) always apply to the
whole sequence: "python NOT java site:x.com" = python, minus java, only x.com.

The parser NEVER raises on user input: an unclosed quote or bracket is closed
automatically and a warning is returned. Recursive descent = one function per
grammar rule, each calling the rules below it.
"""

import re
from dataclasses import dataclass, field

MAX_QUERY_LENGTH = 500
MAX_TOKENS = 64
TEXT_FIELDS = {"title"}
FILTER_FIELDS = {"site", "author"}


# ---------------------------------------------------------------- AST nodes
@dataclass(frozen=True)
class Term:
    word: str
    field: str | None = None
    fuzzy: bool = False


@dataclass(frozen=True)
class Phrase:
    text: str
    field: str | None = None


@dataclass(frozen=True)
class Filter:
    field: str   # "site" or "author"
    value: str


@dataclass(frozen=True)
class Not:
    child: "Node"


@dataclass(frozen=True)
class And:
    children: tuple["Node", ...]


@dataclass(frozen=True)
class Or:
    children: tuple["Node", ...]


Node = Term | Phrase | Filter | Not | And | Or


@dataclass
class ParsedQuery:
    root: Node | None
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- lexer
@dataclass(frozen=True)
class Tok:
    kind: str   # WORD PHRASE FIELD LPAREN RPAREN AND OR NOT MINUS
    value: str = ""
    extra: str = ""  # FIELD: the field name; WORD: "~" if fuzzy


_FIELD_RE = re.compile(r"(title|site|author):", re.IGNORECASE)


def _lex(text: str, warnings: list[str]) -> list[Tok]:
    toks: list[Tok] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch == "(":
            toks.append(Tok("LPAREN")); i += 1
        elif ch == ")":
            toks.append(Tok("RPAREN")); i += 1
        elif ch == '"':
            end = text.find('"', i + 1)
            if end == -1:
                warnings.append("Unclosed quote: closed at the end of the query")
                end = n
            toks.append(Tok("PHRASE", text[i + 1:end]))
            i = end + 1
        elif ch == "-" and i + 1 < n and not text[i + 1].isspace() and (i == 0 or text[i - 1].isspace() or text[i - 1] == "("):
            toks.append(Tok("MINUS")); i += 1
        else:
            m = _FIELD_RE.match(text, i)
            if m and m.end() < n and not text[m.end()].isspace():
                fname = m.group(1).lower()
                i = m.end()
                if text[i] == '"':
                    end = text.find('"', i + 1)
                    if end == -1:
                        warnings.append("Unclosed quote: closed at the end of the query")
                        end = n
                    toks.append(Tok("FIELD", text[i + 1:end], fname + ":phrase"))
                    i = end + 1
                else:
                    j = i
                    while j < n and not text[j].isspace() and text[j] not in '()"':
                        j += 1
                    toks.append(Tok("FIELD", text[i:j], fname))
                    i = j
                continue
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            word = text[i:j]
            i = j
            if word in ("AND", "OR", "NOT"):
                toks.append(Tok(word))
            elif word == "&&":
                toks.append(Tok("AND"))
            elif word == "||":
                toks.append(Tok("OR"))
            elif word.endswith("~") and len(word) > 1:
                toks.append(Tok("WORD", word[:-1], "~"))
            elif any(ch.isalnum() for ch in word):  # punctuation-only "words" (-, ..., !) are dropped
                toks.append(Tok("WORD", word))
    return toks


# ---------------------------------------------------------------- parser
class _Parser:
    def __init__(self, toks: list[Tok], default_op: str, all_fuzzy: bool, warnings: list[str]):
        self.toks, self.i = toks, 0
        self.default_op = default_op
        self.all_fuzzy = all_fuzzy
        self.warnings = warnings
        self.depth = 0

    def peek(self) -> Tok | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self) -> Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def parse(self) -> Node | None:
        node = self.or_expr()
        while self.peek() is not None:  # stray ')' or similar
            t = self.take()
            self.warnings.append(f"Ignored unexpected '{t.value or t.kind.lower()}'")
            more = self.or_expr()
            node = _combine("OR" if self.default_op == "OR" else "AND", [n for n in (node, more) if n])
        return node

    def or_expr(self) -> Node | None:
        parts = [self.seq()]
        while (t := self.peek()) and t.kind == "OR":
            self.take()
            parts.append(self.seq())
        parts = [p for p in parts if p is not None]
        return _combine("OR", parts)

    def seq(self) -> Node | None:
        positives, negatives, filters = [], [], []
        while (t := self.peek()) and t.kind not in ("OR", "RPAREN"):
            node = self.and_expr()
            if node is None:
                continue
            if isinstance(node, Not):
                negatives.append(node)
            elif isinstance(node, Filter):
                filters.append(node)
            else:
                positives.append(node)
        combined = _combine(self.default_op, positives)
        required = ([combined] if combined else []) + filters + negatives
        return _combine("AND", required)

    def and_expr(self) -> Node | None:
        parts = [self.unary()]
        while (t := self.peek()) and t.kind == "AND":
            self.take()
            parts.append(self.unary())
        parts = [p for p in parts if p is not None]
        return _combine("AND", parts)

    def unary(self) -> Node | None:
        t = self.peek()
        if t is None:
            return None
        if t.kind in ("NOT", "MINUS"):
            self.take()
            child = self.unary()
            return Not(child) if child is not None else None
        return self.atom()

    def atom(self) -> Node | None:
        t = self.take()
        if t.kind == "LPAREN":
            self.depth += 1
            if self.depth > 10:
                self.warnings.append("Brackets nested too deeply")
            node = self.or_expr()
            if (nt := self.peek()) and nt.kind == "RPAREN":
                self.take()
            else:
                self.warnings.append("Unclosed bracket: closed at the end of the query")
            self.depth -= 1
            return node
        if t.kind == "PHRASE":
            return Phrase(t.value) if t.value.strip() else None
        if t.kind == "FIELD":
            fname, is_phrase = t.extra.split(":")[0], t.extra.endswith(":phrase")
            if not t.value.strip():
                return None
            if fname in FILTER_FIELDS:
                return Filter(fname, t.value.strip())
            return Phrase(t.value, field=fname) if is_phrase else Term(t.value, field=fname, fuzzy=self.all_fuzzy)
        if t.kind == "WORD":
            return Term(t.value, fuzzy=self.all_fuzzy or t.extra == "~")
        # a dangling operator such as "python AND" or a lone ")"
        self.warnings.append(f"Ignored dangling '{t.kind}'")
        return None


def _combine(op: str, parts: list[Node]) -> Node | None:
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    cls = And if op == "AND" else Or
    flat: list[Node] = []
    for p in parts:  # flatten nested And(And(..)) for simpler evaluation
        flat.extend(p.children if isinstance(p, cls) else [p])
    return cls(tuple(flat))


def parse_query(text: str, mode: str = "basic") -> ParsedQuery:
    """Parse a query. mode: basic (default OR), boolean (default AND),
    phrase (whole query is one phrase), fuzzy (every word fuzzy)."""
    warnings: list[str] = []
    if len(text) > MAX_QUERY_LENGTH:
        warnings.append(f"Query truncated to {MAX_QUERY_LENGTH} characters")
        text = text[:MAX_QUERY_LENGTH]
    if not text.strip():
        return ParsedQuery(None, warnings)
    if mode == "phrase":
        cleaned = text.replace('"', " ").strip()
        return ParsedQuery(Phrase(cleaned) if cleaned else None, warnings)

    toks = _lex(text, warnings)
    if len(toks) > MAX_TOKENS:
        warnings.append(f"Only the first {MAX_TOKENS} query parts were used")
        toks = toks[:MAX_TOKENS]
    default_op = "AND" if mode == "boolean" else "OR"
    root = _Parser(toks, default_op, mode == "fuzzy", warnings).parse()
    return ParsedQuery(root, warnings)


def describe(node: Node | None) -> str:
    """Readable form of the tree, e.g. AND(OR(python, java), NOT(c++))."""
    match node:
        case None:
            return "∅"
        case Term(word=w, field=f, fuzzy=z):
            return f"{f + ':' if f else ''}{w}{'~' if z else ''}"
        case Phrase(text=t, field=f):
            return f'{f + ":" if f else ""}"{t}"'
        case Filter(field=f, value=v):
            return f"{f}:{v}"
        case Not(child=c):
            return f"NOT({describe(c)})"
        case And(children=cs):
            return "AND(" + ", ".join(describe(c) for c in cs) + ")"
        case Or(children=cs):
            return "OR(" + ", ".join(describe(c) for c in cs) + ")"
    return "?"
