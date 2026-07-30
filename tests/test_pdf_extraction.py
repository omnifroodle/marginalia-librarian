"""Smoke coverage for the PDF reader in the vendored pageindex fork.

Added when PyPDF2 was swapped for its maintained rename, `pypdf` (2026-07-30).
Nothing in the suite read a PDF before that, so the swap was invisible to
tests — this file closes the gap for the API surface the fork actually uses:
`PdfReader`, `.pages`, `.extract_text()`, `.metadata.title`.

Deliberately narrow. It proves the parser is wired up and the attribute names
are right; it does *not* prove anything about the real corpus, whose PDFs are
scanned, encrypted, malformed, and CJK in ways a synthesized page is not. The
first full ingest after the swap is still the real test.
"""

from __future__ import annotations

from io import BytesIO

import pymupdf
import pytest

from librarian.pageindex.utils import get_pdf_name


@pytest.fixture
def pdf_bytes() -> BytesIO:
    """A two-page PDF with a known title, built with pymupdf (already a dep)."""
    doc = pymupdf.open()
    for text in ("Hello from page one.", "And page two."):
        doc.new_page().insert_text((72, 100), text)
    doc.set_metadata({"title": "Smoke Test Doc"})
    return BytesIO(doc.tobytes())


def test_reader_extracts_page_text(pdf_bytes):
    import pypdf

    reader = pypdf.PdfReader(pdf_bytes)
    assert len(reader.pages) == 2
    assert [p.extract_text().strip() for p in reader.pages] == [
        "Hello from page one.",
        "And page two.",
    ]


def test_get_pdf_name_reads_the_title_from_a_stream(pdf_bytes):
    # The BytesIO branch of get_pdf_name is the fork's only other use of the
    # reader, and it reaches through `.metadata.title` — a two-hop attribute
    # path that a library rename is exactly the sort of thing to break.
    assert get_pdf_name(pdf_bytes) == "Smoke Test Doc"


def test_get_pdf_name_falls_back_to_basename_for_paths():
    assert get_pdf_name("/vault/demo/some book.pdf") == "some book.pdf"
