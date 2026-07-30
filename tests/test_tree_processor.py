"""Tests for tree flattening, page splitting, and file identity."""

from __future__ import annotations

from pathlib import Path

from librarian.ingestion.tree_processor import (
    file_identity,
    flatten_tree,
    split_labeled_text,
)
from tests.conftest import SAMPLE_TREE_OUTPUT, MockConfig

DOC_ID = "abcdef0123456789"


def _flatten(file_path=Path("sample_report.pdf")):
    return flatten_tree(
        SAMPLE_TREE_OUTPUT, file_path, MockConfig(),
        doc_id=DOC_ID, file_sha256="ff" * 32, file_size=1234,
    )


def test_file_identity_is_content_based(tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    a.write_bytes(b"same bytes")
    b.write_bytes(b"same bytes")
    id_a, sha_a, size_a = file_identity(a)
    id_b, sha_b, size_b = file_identity(b)
    # Identical content → identical identity regardless of filename
    assert id_a == id_b and sha_a == sha_b and size_a == size_b == 10

    b.write_bytes(b"different bytes")
    id_b2, _, _ = file_identity(b)
    assert id_b2 != id_a


def test_split_labeled_text():
    text = (
        "<physical_index_12>\nPage twelve.\n<physical_index_12>\n"
        "<physical_index_13>\nPage thirteen.\n<physical_index_13>\n"
    )
    assert split_labeled_text(text) == [(12, "Page twelve."), (13, "Page thirteen.")]


def test_split_labeled_text_fallback_without_markers():
    assert split_labeled_text("plain text", fallback_page=7) == [(7, "plain text")]
    assert split_labeled_text("   \n  ", fallback_page=7) == []


def test_flatten_tree_produces_correct_counts():
    doc, nodes, content = _flatten()
    # 3 nodes total: 001, 001.001, 002
    assert len(nodes) == 3
    assert doc.node_count == 3


def test_flatten_tree_per_page_content_records():
    doc, nodes, content = _flatten()
    # 001 → pages 1,2,3; 001.001 → pages 1,2; 002 (unlabeled) → 1 fallback record
    by_node = {}
    for c in content:
        by_node.setdefault(c.node_id, []).append(c.page_number)
    assert sorted(by_node["001"]) == [1, 2, 3]
    assert sorted(by_node["001.001"]) == [1, 2]
    assert by_node["002"] == [4]  # falls back to the node's start_index

    # Page records carry clean text, no markers
    assert all("physical_index" not in c.content for c in content)
    page2 = next(c for c in content if c.node_id == "001" and c.page_number == 2)
    assert page2.content == "Operating margins expanded to 31%."


def test_flatten_tree_page_mapping():
    doc, nodes, content = _flatten()
    root = next(n for n in nodes if n.node_id == "001")
    assert root.start_page == 1
    assert root.end_page == 3


def test_flatten_tree_depth_and_order():
    doc, nodes, content = _flatten()
    root_nodes = [n for n in nodes if n.depth == 0]
    child_nodes = [n for n in nodes if n.depth == 1]
    assert len(root_nodes) == 2
    assert len(child_nodes) == 1
    assert child_nodes[0].parent_node_id == "001"


def test_flatten_tree_is_leaf():
    doc, nodes, content = _flatten()
    leaf = next(n for n in nodes if n.node_id == "001.001")
    assert leaf.is_leaf is True
    parent = next(n for n in nodes if n.node_id == "001")
    assert parent.is_leaf is False


def test_flatten_tree_top_level_titles():
    doc, nodes, content = _flatten()
    assert "Executive Summary" in doc.top_level_titles
    assert "Risk Factors" in doc.top_level_titles


def test_flatten_tree_doc_record_fields():
    doc, nodes, content = _flatten()
    assert doc.doc_id == DOC_ID
    assert doc.doc_name == "sample_report"
    assert doc.description == "A sample financial report for testing."
    assert doc.source_type == "pdf"
    assert doc.tree_depth == 1
    assert doc.page_count == 8  # max end_page seen
    assert doc.file_sha256 == "ff" * 32
    assert doc.file_size == 1234
    assert doc.source_path == "sample_report.pdf"


def test_flatten_tree_epub_source_type():
    doc, nodes, content = _flatten(Path("my_book.epub"))
    assert doc.source_type == "epub"


def test_flatten_tree_markdown_source_type():
    doc, nodes, content = _flatten(Path("notes.md"))
    assert doc.source_type == "markdown"


def test_doc_name_keeps_dots_that_are_not_extensions():
    """EPUBs supply their Dublin Core title as doc_name — "Graph Databases:
    Vol. 2" must not be truncated to "Graph Databases: Vol"."""
    raw = dict(SAMPLE_TREE_OUTPUT, doc_name="Graph Databases: Vol. 2")
    doc, _nodes, _content = flatten_tree(
        raw, Path("book.epub"), MockConfig(), doc_id=DOC_ID
    )
    assert doc.doc_name == "Graph Databases: Vol. 2"

    from_filename = flatten_tree(
        dict(SAMPLE_TREE_OUTPUT, doc_name="sample_report.pdf"),
        Path("sample_report.pdf"), MockConfig(), doc_id=DOC_ID,
    )[0]
    assert from_filename.doc_name == "sample_report"


# ── EPUB: chapter-shaped raw output ──────────────────────────────────────────

EPUB_TREE_OUTPUT = {
    "doc_name": "Graph Databases",
    "doc_description": "A book about graphs.",
    "structure": [
        {
            "title": "Data Modeling",
            "node_id": "0001",
            "text": "Graphs model relationships directly.",
            "start_index": 1,
            "end_index": 1,
            "chapter_href": "ch01.html",
            "heading_anchor": "chapter1",
            "nodes": [
                {
                    "title": "Nodes",
                    "node_id": "0002",
                    "text": "A node is an entity.",
                    "start_index": 1,
                    "end_index": 1,
                    "chapter_href": "ch01.html",
                    "heading_anchor": "idm100",
                },
            ],
        },
        {
            "title": "Colophon",
            "node_id": "0003",
            "text": "Set in DejaVu.",
            "start_index": 2,
            "end_index": 2,
            "chapter_href": "ch02.html",
        },
    ],
    "chapters": [
        {"index": 1, "href": "ch01.html", "title": "Data Modeling",
         "html": "<div id='chapter1'><h1>Data Modeling</h1></div>"},
        {"index": 2, "href": "ch02.html", "title": "Colophon",
         "html": "<div><p>Set in DejaVu.</p></div>"},
    ],
}


def _flatten_epub():
    return flatten_tree(
        EPUB_TREE_OUTPUT, Path("graphdb.epub"), MockConfig(), doc_id=DOC_ID
    )


def test_epub_nodes_carry_their_chapter():
    _doc, nodes, _content = _flatten_epub()

    by_id = {n.node_id: n for n in nodes}
    assert by_id["0002"].chapter_href == "ch01.html"
    assert by_id["0002"].heading_anchor == "idm100"
    assert by_id["0003"].chapter_href == "ch02.html"
    # page_number is the chapter's spine position for EPUBs
    assert (by_id["0003"].start_page, by_id["0003"].end_page) == (2, 2)


def test_epub_text_records_carry_their_chapter():
    _doc, _nodes, content = _flatten_epub()

    text_records = [r for r in content if r.content]
    assert {r.chapter_href for r in text_records} == {"ch01.html", "ch02.html"}
    assert all(r.content_html is None for r in text_records)


def test_epub_emits_one_html_record_per_chapter():
    _doc, _nodes, content = _flatten_epub()

    html_records = [r for r in content if r.content_html]
    assert len(html_records) == 2
    assert [r.node_id for r in html_records] == ["chapter:1", "chapter:2"]
    assert [r.chapter_href for r in html_records] == ["ch01.html", "ch02.html"]


def test_chapter_html_records_are_invisible_to_bm25():
    """content is empty, so content_search analyzes to zero tokens and the
    record can never match a search_pages query."""
    _doc, _nodes, content = _flatten_epub()

    for record in (r for r in content if r.content_html):
        assert record.content == ""
        assert record.to_os_doc()["content_search"] == ""


def test_chapter_html_records_cannot_collide_with_node_records():
    """The _id is doc_id::node_id::page_number — a chapter record shares a
    page_number with the nodes in that chapter, so its node_id must not."""
    _doc, nodes, content = _flatten_epub()

    ids = [f"{DOC_ID}::{r.node_id}::{r.page_number}" for r in content]
    assert len(ids) == len(set(ids))
    assert {n.node_id for n in nodes}.isdisjoint({"chapter:1", "chapter:2"})


def test_chapter_html_records_are_never_returned_by_node_lookups():
    """The note generator feeds on get_page_content(doc_id, node_ids), so a
    record with no text must not be addressable by any real node_id."""
    _doc, nodes, content = _flatten_epub()

    node_ids = {n.node_id for n in nodes}
    for record in (r for r in content if r.content_html):
        assert record.node_id not in node_ids


def test_pdf_output_gets_no_chapter_records():
    _doc, _nodes, content = _flatten()

    assert all(r.content_html is None for r in content)
    assert all(r.chapter_href is None for r in content)
