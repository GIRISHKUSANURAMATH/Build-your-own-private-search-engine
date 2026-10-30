"""
POST /analyze — show every stage of the text pipeline for some input.

A teaching and debugging tool: paste text or HTML in Swagger (/docs) and see
exactly what the index will receive. Nothing is stored.
"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator

from app.indexing.analyzer import Analyzer, trace_as_dicts
from app.indexing.cleaner import clean_text
from app.indexing.html_extractor import extract_html

router = APIRouter(tags=["text processing"])

MAX_INPUT_CHARS = 200_000  # keep the endpoint cheap; real documents go through the indexer


class AnalyzeRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_INPUT_CHARS, description="Plain text or HTML")
    format: Literal["text", "html"] = "text"
    remove_stopwords: bool = True
    stem: bool = True

    model_config = {
        "json_schema_extra": {
            "examples": [{"text": "Python's powerful programming languages!", "format": "text"}]
        }
    }

    @model_validator(mode="after")
    def _not_blank(self):
        if not self.text.strip():
            raise ValueError("text must not be blank")
        return self


class TokenOut(BaseModel):
    original: str
    normalized: str
    term: str | None
    position: int
    start: int
    end: int
    dropped: str | None


class ExtractedOut(BaseModel):
    title: str
    description: str | None
    author: str | None
    language: str | None
    canonical_url: str | None
    headings: list[str]


class AnalyzeResponse(BaseModel):
    analyzer: dict
    extracted: ExtractedOut | None  # only for format="html"
    cleaned_text: str
    tokens: list[TokenOut]          # every token, including dropped ones
    terms: list[str]                # what would go into the index
    stats: dict


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    extracted = None
    if req.format == "html":
        doc = extract_html(req.text)
        cleaned = doc.text
        extracted = ExtractedOut(
            title=doc.title, description=doc.description, author=doc.author,
            language=doc.language, canonical_url=doc.canonical_url, headings=doc.headings,
        )
    else:
        cleaned = clean_text(req.text)

    analyzer = Analyzer(remove_stopwords=req.remove_stopwords, stem=req.stem)
    traces = analyzer.trace(cleaned)
    terms = [t.term for t in traces if t.term is not None]
    return AnalyzeResponse(
        analyzer=analyzer.config(),
        extracted=extracted,
        cleaned_text=cleaned,
        tokens=[TokenOut(**t) for t in trace_as_dicts(traces)],
        terms=terms,
        stats={
            "tokens": len(traces),
            "terms": len(terms),
            "unique_terms": len(set(terms)),
            "stopwords_removed": sum(t.dropped == "stopword" for t in traces),
        },
    )
