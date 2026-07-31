"""Generate per-citation blurbs and liner notes.

Replaces the answer-synthesis step of classic RAG: instead of one generated
answer, each selected node becomes a citation with a short "why this applies"
blurb and margin notes quoting the source verbatim.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import litellm

from ..config import Config
from .quote_resolver import ResolvedQuote, resolve_quote

_log = logging.getLogger("librarian.notes")


@dataclass
class LinerNote:
    quote: str
    comment: str
    anchor: ResolvedQuote


@dataclass
class CitationNotes:
    doc_id: str
    node_id: str
    doc_name: str
    title: str
    rationale: str  # from the tree reasoner
    blurb: str = ""
    liner_notes: list[LinerNote] = field(default_factory=list)
    start_page: Optional[int] = None
    end_page: Optional[int] = None
    chapter_href: Optional[str] = None  # EPUB: the chapter this section is in
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "node_id": self.node_id,
            "doc_name": self.doc_name,
            "title": self.title,
            "rationale": self.rationale,
            "blurb": self.blurb,
            "start_page": self.start_page,
            "end_page": self.end_page,
            "chapter_href": self.chapter_href,
            "error": self.error,
            "liner_notes": [
                {
                    "quote": n.quote,
                    "comment": n.comment,
                    "page_number": n.anchor.page_number,
                    "chapter_href": n.anchor.chapter_href,
                    "char_start": n.anchor.char_start,
                    "char_end": n.anchor.char_end,
                    "anchor_status": n.anchor.status,
                }
                for n in self.liner_notes
            ],
        }


def _locator_label(record: dict) -> str:
    """How a block of section text is labelled for the model. PDFs get a page,
    EPUBs get their chapter. The model echoes it back as `page_hint`, which is
    advisory only — the quote resolver decides the real anchor — so this is
    purely about giving the model a sane handle on where it is."""
    chapter = record.get("chapter_href")
    if chapter:
        return f"chapter {chapter}"
    return f"page {record.get('page_number', '?')}"


class NoteGenerator:
    def __init__(self, config: Config) -> None:
        self._config = config

    def generate(
        self,
        query: str,
        *,
        doc_id: str,
        node_id: str,
        doc_name: str,
        title: str,
        rationale: str,
        page_records: list[dict],
        start_page: int | None = None,
        end_page: int | None = None,
        chapter_href: str | None = None,
    ) -> CitationNotes:
        """One LLM call: blurb + liner notes for a single selected node.

        Every emitted quote is verified against the stored page records;
        unresolved quotes keep their note but get a node-level anchor.
        """
        result = CitationNotes(
            doc_id=doc_id, node_id=node_id, doc_name=doc_name, title=title,
            rationale=rationale, start_page=start_page, end_page=end_page,
            chapter_href=chapter_href,
        )

        content = "\n\n".join(
            f"[{_locator_label(r)}]\n{r.get('content', '')}" for r in page_records
        )
        max_notes = self._config.max_liner_notes_per_citation

        prompt = f"""You are a research librarian preparing a section of a book for a reader
who asked a specific question. You do NOT answer the question yourself — you leave notes in
the margins that help the reader find and understand the relevant passages.

Reader's question: {query}

Book: {doc_name}
Section: {title}
Why this section was pulled: {rationale or "n/a"}

Section text (with location markers):
{content}

Produce JSON with exactly these fields:
- "blurb": one or two sentences telling the reader why this section is worth opening for
  their question. Written to appear in a citation list.
- "liner_notes": an array of 1-{max_notes} margin notes. Each has:
  - "quote": a short VERBATIM excerpt copied exactly from the section text above
    (one clause to two sentences; never paraphrase, never include location markers)
  - "comment": one or two sentences connecting that passage to the reader's question
  - "page_hint": the location marker the quote appears under, copied as-is

Choose passages that most directly bear on the question. Return ONLY the JSON object."""

        try:
            response = litellm.completion(
                model=self._config.notes_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                api_base=self._config.reasoning_api_base,
                api_key=self._config.reasoning_api_key,
                num_retries=self._config.num_retries,
                timeout=self._config.completion_timeout,
            )
            raw = response.choices[0].message.content.strip()
        except Exception as e:
            _log.error("note generation failed for %s/%s: %s", doc_id, node_id, e)
            result.error = str(e)
            result.blurb = rationale  # degrade to the reasoner's rationale
            return result

        from ..query.tree_reasoner import _parse_llm_json
        parsed = _parse_llm_json(raw, "note_generation", self._config.notes_model, self._config.log_dir)

        if not isinstance(parsed, dict):
            result.error = "unparseable note response"
            result.blurb = rationale
            return result

        result.blurb = str(parsed.get("blurb", "")).strip() or rationale

        notes = parsed.get("liner_notes") or []
        if not isinstance(notes, list):
            notes = []
        for n in notes[:max_notes]:
            if not isinstance(n, dict):
                continue
            quote = str(n.get("quote", "")).strip()
            comment = str(n.get("comment", "")).strip()
            if not quote or not comment:
                continue
            # page_hint is advisory only — the resolver decides the real anchor.
            anchor = resolve_quote(quote, page_records)
            if anchor.status == "unresolved":
                _log.info("unresolved quote in %s/%s: %r", doc_id, node_id, quote[:80])
            result.liner_notes.append(LinerNote(quote=quote, comment=comment, anchor=anchor))

        return result
