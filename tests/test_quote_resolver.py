"""Tests for quote → page/offset resolution."""

from __future__ import annotations

from librarian.notes.quote_resolver import find_quote, normalize, resolve_quote


def test_normalize_folds_whitespace_ligatures_case():
    assert normalize("The  ﬁghter\nmay   grapple") == "the fighter may grapple"
    assert normalize("soft­hyphen") == "softhyphen"
    assert normalize("“smart” quotes — and dashes") == '"smart" quotes - and dashes'


def test_find_quote_returns_original_offsets():
    content = "Rules of Combat.\nThe  ﬁghter may\ngrapple a foe of similar size."
    span = find_quote("the fighter may grapple", content)
    assert span is not None
    start, end = span
    assert content[start:end] == "The  ﬁghter may\ngrapple"


def test_find_quote_missing():
    assert find_quote("not present at all", "some other text") is None


def test_resolve_quote_picks_correct_page():
    pages = [
        {"node_id": "0003", "page_number": 41, "content": "Intro to combat."},
        {"node_id": "0003", "page_number": 42, "content": "The fighter may grapple a foe."},
    ]
    r = resolve_quote("fighter may grapple", pages)
    assert r.status == "resolved"
    assert r.page_number == 42
    assert r.char_start is not None


def test_resolve_quote_unresolved_keeps_node_context():
    pages = [{"node_id": "0003", "page_number": 41, "content": "Intro to combat."}]
    r = resolve_quote("completely absent text", pages)
    assert r.status == "unresolved"
    assert r.page_number == 41
    assert r.node_id == "0003"
    assert r.char_start is None


def test_resolve_quote_empty_pages():
    r = resolve_quote("anything", [])
    assert r.status == "unresolved"
    assert r.page_number is None
