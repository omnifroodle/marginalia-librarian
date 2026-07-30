"""EPUB chapter extraction and serve-time rendering.

The fixture is a synthetic EPUB written with ebooklib rather than a binary blob
checked into the repo. It deliberately reproduces the traps found in a real
O'Reilly book: every heading is an <h1> regardless of nesting, anchors live on
wrapper divs instead of the headings, and one chapter has no heading at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ebooklib import epub as ebooklib_epub

from librarian.epub import (
    build_markdown,
    extract_chapters,
    render_chapter_html,
    zip_path_for,
)

# Chapter 1: nested sections, anchors on the wrapper divs, an <h1> at every
# level (the flat-tree trap), and an image with a chapter-relative src.
CH1 = """<html><body>
<section data-type="chapter">
  <div class="chapter" id="chapter1">
    <h1>Data Modeling</h1>
    <p>Graphs model relationships directly.</p>
    <div class="sect1" id="idm100">
      <h1>Nodes</h1>
      <p>A node is an entity.</p>
      <img src="assets/fig01.png" alt="a node"/>
      <div class="sect2" id="idm200">
        <h1>Labels</h1>
        <p>Labels group nodes by role.</p>
      </div>
    </div>
  </div>
</section>
</body></html>"""

# Chapter 2: no heading anywhere — its title has to come from the ToC, or its
# text gets absorbed into the previous chapter's node.
CH2 = """<html><body>
<div class="preface">
  <p>Some front matter with no heading of its own.</p>
</div>
</body></html>"""


@pytest.fixture
def epub_path(tmp_path: Path) -> Path:
    book = ebooklib_epub.EpubBook()
    book.set_identifier("test-epub")
    book.set_title("Graph Databases: Vol. 2")
    book.set_language("en")

    c1 = ebooklib_epub.EpubHtml(title="Data Modeling", file_name="ch01.html")
    c1.content = CH1
    c2 = ebooklib_epub.EpubHtml(title="Colophon", file_name="ch02.html")
    c2.content = CH2

    image = ebooklib_epub.EpubImage(
        uid="fig01", file_name="assets/fig01.png",
        media_type="image/png", content=b"\x89PNG\r\n\x1a\n fake",
    )

    for item in (c1, c2, image):
        book.add_item(item)
    book.toc = (
        ebooklib_epub.Link("ch01.html", "Data Modeling", "ch01"),
        ebooklib_epub.Link("ch02.html", "Colophon", "ch02"),
    )
    book.spine = [c1, c2]
    book.add_item(ebooklib_epub.EpubNcx())

    path = tmp_path / "book.epub"
    ebooklib_epub.write_epub(str(path), book)
    return path


# ── extraction ───────────────────────────────────────────────────────────────

def test_extracts_chapters_in_spine_order(epub_path):
    doc_name, chapters = extract_chapters(epub_path)

    assert doc_name == "Graph Databases: Vol. 2"
    assert [c.index for c in chapters] == [1, 2]
    assert [c.href for c in chapters] == ["ch01.html", "ch02.html"]


def test_heading_levels_follow_section_nesting_not_the_tag(epub_path):
    """The whole reason this rework exists: every heading in the source is an
    <h1>, so trusting the tag would flatten the tree into siblings and leave the
    agentic reasoner nothing to navigate."""
    _doc_name, chapters = extract_chapters(epub_path)

    levels = [(h.text, h.level) for h in chapters[0].headings]
    assert levels == [("Data Modeling", 1), ("Nodes", 2), ("Labels", 3)]


def test_anchors_come_from_the_wrapper_div(epub_path):
    _doc_name, chapters = extract_chapters(epub_path)

    anchors = {h.text: h.anchor for h in chapters[0].headings}
    assert anchors == {"Data Modeling": "chapter1", "Nodes": "idm100", "Labels": "idm200"}


def test_headingless_chapter_gets_a_title_from_the_toc(epub_path):
    """Without an injected heading this chapter's text is silently absorbed into
    the previous chapter's node."""
    _doc_name, chapters = extract_chapters(epub_path)

    colophon = chapters[1]
    assert colophon.title == "Colophon"
    assert colophon.markdown.startswith("# Colophon")
    assert [h.level for h in colophon.headings] == [1]


def test_every_chapter_starts_at_level_one(epub_path):
    """What guarantees a tree node never straddles a chapter boundary."""
    _doc_name, chapters = extract_chapters(epub_path)

    assert all(c.headings[0].level == 1 for c in chapters)


def test_image_src_becomes_a_zip_relative_path(epub_path):
    _doc_name, chapters = extract_chapters(epub_path)

    assert 'src="EPUB/assets/fig01.png"' in chapters[0].html


def test_markdown_carries_the_prose(epub_path):
    _doc_name, chapters = extract_chapters(epub_path)

    assert "Graphs model relationships directly." in chapters[0].markdown
    assert "Labels group nodes by role." in chapters[0].markdown


# ── the line map md_to_tree's node.line_num is looked up in ───────────────────

def test_build_markdown_maps_heading_lines_back_to_chapter_and_anchor(epub_path):
    _doc_name, chapters = extract_chapters(epub_path)

    markdown, anchors = build_markdown(chapters)
    lines = markdown.split("\n")

    for line_num, (chapter, heading) in anchors.items():
        # line_num is 1-based, matching what md_to_tree reports as node.line_num
        assert lines[line_num - 1].lstrip("#").strip() == heading.text
        assert chapter.href.endswith(".html")

    by_text = {h.text: (c, h) for c, h in anchors.values()}
    assert by_text["Labels"][0].href == "ch01.html"
    assert by_text["Labels"][1].anchor == "idm200"
    assert by_text["Colophon"][0].href == "ch02.html"


def test_zip_path_for_resolves_against_the_chapter_directory():
    assert zip_path_for("assets/fig01.png", "OEBPS") == "OEBPS/assets/fig01.png"
    assert zip_path_for("../images/x.png", "OEBPS/text") == "OEBPS/images/x.png"
    assert zip_path_for("ch01.html", "") == "ch01.html"


# ── serve-time rendering ─────────────────────────────────────────────────────

def test_render_strips_scripts_and_event_handlers():
    html = (
        '<div id="chapter1"><script>alert(1)</script>'
        '<p onclick="steal()">Hello</p>'
        '<a href="javascript:evil()">click</a></div>'
    )

    out = render_chapter_html(html, "/assets/abc/media")

    assert "<script" not in out
    assert "alert(1)" not in out
    assert "onclick" not in out
    assert "javascript:" not in out
    assert "Hello" in out


def test_render_keeps_ids_so_citations_can_deep_link():
    out = render_chapter_html('<div class="sect1" id="idm100"><h2>Nodes</h2></div>', "/m")

    assert 'id="idm100"' in out
    assert "<h2>" in out


def test_render_points_images_at_the_media_route():
    out = render_chapter_html('<img src="OEBPS/assets/fig01.png" alt="fig"/>', "/assets/abc/media")

    assert 'src="/assets/abc/media/OEBPS/assets/fig01.png"' in out
    assert 'alt="fig"' in out


def test_render_leaves_absolute_urls_alone():
    out = render_chapter_html('<img src="https://example.com/x.png"/>', "/assets/abc/media")

    assert 'src="https://example.com/x.png"' in out
