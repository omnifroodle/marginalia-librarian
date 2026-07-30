"""Resolve LLM-emitted quotes back to positions in stored page content.

The NoteGenerator asks the model for verbatim quotes, but models paraphrase
whitespace, ligatures, and hyphenation. Matching is done over a normalized
view of both quote and page text, with an offset map so matches translate
back to positions in the original stored content.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

_LIGATURES = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "’": "'",
    "‘": "'",
    "“": '"',
    "”": '"',
    "–": "-",
    "—": "-",
}

_SOFT_HYPHEN = "­"


@dataclass
class ResolvedQuote:
    quote: str
    status: str  # "resolved" | "unresolved"
    page_number: Optional[int] = None  # PDF: page the quote starts on
    chapter_href: Optional[str] = None  # EPUB: chapter the quote is in
    node_id: Optional[str] = None
    char_start: Optional[int] = None  # offset into the matched record's content
    char_end: Optional[int] = None


def _normalize_with_offsets(text: str) -> tuple[str, list[int]]:
    """Return (normalized_text, offsets) where offsets[i] is the index in the
    original text that produced normalized char i."""
    out: list[str] = []
    offsets: list[int] = []
    prev_space = False
    for i, ch in enumerate(text):
        if ch == _SOFT_HYPHEN:
            continue
        if ch in _LIGATURES:
            for sub in _LIGATURES[ch]:
                out.append(sub.lower())
                offsets.append(i)
            prev_space = False
            continue
        if ch.isspace():
            if prev_space:
                continue
            out.append(" ")
            offsets.append(i)
            prev_space = True
            continue
        prev_space = False
        lowered = ch.lower()
        for sub in lowered:  # casefolding can expand (e.g. ß)
            out.append(sub)
            offsets.append(i)
    # strip leading/trailing space
    start = 0
    end = len(out)
    if out and out[0] == " ":
        start = 1
    if end > start and out[end - 1] == " ":
        end -= 1
    return "".join(out[start:end]), offsets[start:end]


def normalize(text: str) -> str:
    """Normalized view used for matching (whitespace/ligature/case folded)."""
    normalized, _ = _normalize_with_offsets(text)
    return normalized


def find_quote(quote: str, content: str) -> tuple[int, int] | None:
    """Locate quote in content; returns (char_start, char_end) in the ORIGINAL
    content, or None."""
    norm_quote = normalize(quote)
    if not norm_quote:
        return None
    norm_content, offsets = _normalize_with_offsets(content)
    idx = norm_content.find(norm_quote)
    if idx == -1:
        return None
    start = offsets[idx]
    last = idx + len(norm_quote) - 1
    end = offsets[last] + 1
    return start, end


def resolve_quote(quote: str, page_records: list[dict]) -> ResolvedQuote:
    """Match a quote against a node's page_content records.

    page_records are OpenSearch _source dicts with node_id, page_number, content.
    Returns a resolved anchor, or an unresolved marker that still carries the
    node context of the first record (the note renders without a highlight).
    """
    for rec in page_records:
        span = find_quote(quote, rec.get("content", ""))
        if span:
            return ResolvedQuote(
                quote=quote,
                status="resolved",
                page_number=rec.get("page_number"),
                chapter_href=rec.get("chapter_href"),
                node_id=rec.get("node_id"),
                char_start=span[0],
                char_end=span[1],
            )
    first = page_records[0] if page_records else {}
    return ResolvedQuote(
        quote=quote,
        status="unresolved",
        page_number=first.get("page_number"),
        chapter_href=first.get("chapter_href"),
        node_id=first.get("node_id"),
    )
