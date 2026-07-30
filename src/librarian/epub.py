"""Everything EPUB: chapter extraction at ingest, sanitization at serve time.

Split in two halves on purpose:

- `extract_chapters()` runs at ingest. It is a pure function of the file — no
  LLM, no OpenSearch — which is what makes the chapter/heading/anchor logic
  testable without stubbing a model.
- `render_chapter_html()` runs per request. Sanitizing at serve time rather than
  freezing sanitized HTML into the index means a presentation fix (a tighter
  allowlist, a different image route) never costs a re-ingest — and ingest is
  where the money is, since it runs the whole PageIndex tree build.

Stored chapter HTML therefore carries `<img src>` as a *zip-relative path*
("OEBPS/assets/x.png"); the serve half rewrites those to real URLs.
"""

from __future__ import annotations

import logging
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

import ebooklib
import nh3
from bs4 import BeautifulSoup
from ebooklib import epub as ebooklib_epub
from markdownify import markdownify

_log = logging.getLogger("librarian.epub")

# Elements that introduce a level of document structure. EPUBs disagree about
# how to nest: some wrap in <section>, some in <div class="sect1">, O'Reilly's
# do both. Depth is counted over whatever is present and then rebased per
# chapter (see _heading_levels), so only the *relative* nesting matters.
_SECTION_CLASS = re.compile(r"^(chapter|part|appendix|preface|foreword|sect\d+|sidebar)$")

# Dropped from the stored HTML outright — never useful, and <script>/<style>
# would only have to be stripped again later.
_DROP_TAGS = ("script", "style", "link", "meta", "base")

_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")

# Media types a spine entry may legitimately carry (see _is_document).
_HTML_MEDIA_TYPES = {"application/xhtml+xml", "text/html", "application/x-dtbook+xml"}

# Markdown ATX heading, e.g. "## Title" — mirrors the pattern page_index_md
# uses to find nodes, including its fenced-code guard.
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
_MD_FENCE = re.compile(r"^```")

_ALLOWED_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "cite", "code", "col",
    "colgroup", "dd", "div", "dl", "dt", "em", "figcaption", "figure", "h1",
    "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "li", "ol", "p", "pre",
    "q", "s", "section", "small", "span", "strong", "sub", "sup", "table",
    "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul",
}

# `id` is kept on everything: heading anchors live on wrapper divs, and they are
# what a citation deep-links to.
_ALLOWED_ATTRS = {
    "*": {"id", "class"},
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
}


@dataclass
class Heading:
    """One heading in a chapter, with the anchor a citation can link to."""

    level: int  # rebased: the chapter's own title is always 1
    text: str
    anchor: str | None  # id on the heading, else its nearest ancestor's


@dataclass
class Chapter:
    index: int  # 1-based spine position; becomes page_number in the index
    href: str  # OPF-relative, e.g. "ch03.html" — the chapter_href the reader uses
    title: str
    html: str  # lightly cleaned, <img src> rewritten to zip-relative paths
    markdown: str
    headings: list[Heading] = field(default_factory=list)
    # Line of each heading within this chapter's markdown (0-based, parallel to
    # `headings`). Lets a tree node's line_num be traced back to its anchor.
    heading_lines: list[int] = field(default_factory=list)


# ── ingest half ──────────────────────────────────────────────────────────────

def opf_dir(file_path: Path) -> str:
    """Directory the OPF lives in — chapter and image hrefs are relative to it,
    while zip entries are not (an href of "ch03.html" is "OEBPS/ch03.html")."""
    with zipfile.ZipFile(file_path) as zf:
        container = zf.read("META-INF/container.xml")
    root = ElementTree.fromstring(container)
    rootfile = root.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
    full_path = rootfile.get("full-path", "") if rootfile is not None else ""
    return posixpath.dirname(full_path)


def zip_path_for(href: str, base_dir: str) -> str:
    """Resolve an href appearing inside a chapter to its entry in the archive."""
    return posixpath.normpath(posixpath.join(base_dir, href)) if base_dir else href


def _toc_titles(toc) -> dict[str, str]:
    """href (without fragment) → title, flattened over the ToC.

    ebooklib hands back a ragged structure: nested (Section, [Link, ...]) tuples,
    a bare list, or — for a single-entry ToC — one Link on its own.
    """
    titles: dict[str, str] = {}

    def visit(node) -> None:
        if isinstance(node, (list, tuple)):
            for item in node:
                visit(item)
        elif getattr(node, "href", None):
            titles.setdefault(node.href.split("#")[0], node.title)

    visit(toc)
    return titles


def _is_document(item) -> bool:
    """Whether a spine entry is a content document.

    Not just `get_type() == ITEM_DOCUMENT`: ebooklib derives that from the
    manifest's media-type, so an EPUB that declares its chapters as `text/html`
    rather than `application/xhtml+xml` reads back as untyped items and every
    chapter is silently skipped — the book ingests as empty.
    """
    if item.get_type() == ebooklib.ITEM_DOCUMENT:
        return True
    return (getattr(item, "media_type", "") or "").lower() in _HTML_MEDIA_TYPES


def _text_of(value: str) -> str:
    return " ".join(str(value).split())


def _heading_text(heading) -> str:
    """Heading text with its inner markup spaced, not glued.

    `get_text(strip=True)` would turn `<span class="label">Chapter 1. </span>
    Introduction` into "Chapter 1.Introduction" — the label span's trailing space
    is exactly what it strips.
    """
    return _text_of(heading.get_text(" ", strip=True))


def _anchor_for(heading) -> str | None:
    """The id a citation would link to: the heading's own, else the nearest
    ancestor carrying one (O'Reilly EPUBs put it on the wrapper div)."""
    if heading.get("id"):
        return heading["id"]
    for parent in heading.parents:
        if parent.get and parent.get("id"):
            return parent["id"]
    return None


def _section_depth(element) -> int:
    depth = 0
    for parent in element.parents:
        if parent.name == "section":
            depth += 1
        elif parent.name == "div":
            classes = parent.get("class") or []
            if any(_SECTION_CLASS.match(c) for c in classes):
                depth += 1
    return depth


def _heading_levels(headings: list) -> list[int]:
    """Heading levels from section nesting, compressed to 1..n per chapter.

    The tag itself is useless here: O'Reilly EPUBs mark the chapter title *and*
    each of its sections as <h1>, which would flatten the whole tree into
    siblings and leave the agentic reasoner nothing to navigate.

    Raw nesting depth isn't usable directly either — a book that wraps in both
    <section> and <div class="sect1"> gains two levels per step, so depths come
    out as 2, 4, 6, 8… and everything past the 6th markdown heading level gets
    clamped together, re-flattening the deep sections we just recovered. Ranking
    the *distinct* depths within the chapter preserves the nesting exactly while
    keeping levels consecutive, and starting at 1 keeps chapters from nesting
    inside one another when some wrap in <section> and others don't.
    """
    if not headings:
        return []
    depths = [_section_depth(h) for h in headings]
    rank = {depth: i + 1 for i, depth in enumerate(sorted(set(depths)))}
    return [min(rank[d], 6) for d in depths]


def _markdown_heading_lines(markdown: str) -> list[int]:
    """0-based lines of the ATX headings in `markdown`, skipping fenced code."""
    lines: list[int] = []
    in_fence = False
    for i, line in enumerate(markdown.split("\n")):
        stripped = line.strip()
        if _MD_FENCE.match(stripped):
            in_fence = not in_fence
            continue
        if not in_fence and _MD_HEADING.match(stripped):
            lines.append(i)
    return lines


def extract_chapters(file_path: Path) -> tuple[str, list[Chapter]]:
    """Parse an EPUB into (doc_name, chapters) in spine order.

    Each chapter is guaranteed to begin with a level-1 heading, which is what
    lets every tree node belong to exactly one chapter downstream: a node runs
    from its heading to the next, and the next chapter always opens with one.
    (It also fixes a real bug — a heading-less chapter's text used to be
    absorbed into the *previous* chapter's node.)
    """
    book = ebooklib_epub.read_epub(str(file_path))
    base_dir = opf_dir(file_path)

    title_meta = book.get_metadata("DC", "title")
    doc_name = title_meta[0][0] if title_meta else file_path.stem

    toc_titles = _toc_titles(book.toc)

    items_by_id = {item.get_id(): item for item in book.get_items()}
    ordered = [
        items_by_id[item_id]
        for item_id, _linear in book.spine
        if item_id in items_by_id and _is_document(items_by_id[item_id])
    ]

    chapters: list[Chapter] = []
    for item in ordered:
        chapter = _extract_one(item, base_dir, toc_titles, index=len(chapters) + 1)
        if chapter:
            chapters.append(chapter)

    return doc_name, chapters


def _extract_one(item, base_dir: str, toc_titles: dict[str, str], index: int) -> Chapter | None:
    href = item.get_name()
    soup = BeautifulSoup(item.get_content(), "html.parser")

    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()

    # Images: resolve against the chapter's own directory, and store the archive
    # path. The serve half turns these into URLs.
    chapter_dir = posixpath.dirname(zip_path_for(href, base_dir))
    for img in soup.find_all("img"):
        src = img.get("src")
        if src and not src.startswith(("http://", "https://", "data:")):
            img["src"] = zip_path_for(src, chapter_dir)

    body = soup.body or soup
    headings = body.find_all(_HEADING_TAGS)
    levels = _heading_levels(headings)

    fallback_title = (
        toc_titles.get(href)
        or (soup.title.string if soup.title else "")
        # Better than "Section 4" for the cover/ad/colophon pages that carry
        # neither a ToC entry nor a heading — they still show up in the ToC.
        or PurePosixPath(href).stem.replace("-", " ").replace("_", " ").title()
    )
    fallback_title = _text_of(fallback_title)

    for heading, level in zip(headings, levels):
        heading.name = f"h{level}"

    title = _heading_text(headings[0]) if headings else fallback_title
    markdown = markdownify(str(body), heading_style="ATX").strip()

    # Every chapter must open with a level-1 heading (see the docstring).
    if not headings or levels[0] != 1:
        markdown = f"# {fallback_title}\n\n{markdown}"
        prepended = True
        title = fallback_title if not headings else title
    else:
        prepended = False

    if not markdown.strip():
        return None

    chapter = Chapter(
        index=index,
        href=href,
        title=title,
        html=str(body),
        markdown=markdown,
    )
    _attach_anchors(chapter, headings, levels, prepended, fallback_title)
    return chapter


def _attach_anchors(chapter: Chapter, headings, levels, prepended: bool, fallback_title: str) -> None:
    """Pair the markdown's heading lines with the DOM headings, in order.

    Positional rather than text-matching: markdownify escapes and reflows
    heading text, so comparing strings is fragile. If the counts disagree
    (markdownify dropped an empty heading, say), drop this chapter's anchors
    rather than mis-assigning them — a chapter with no anchors still reads fine.
    """
    dom = [Heading(level=level, text=_heading_text(h), anchor=_anchor_for(h))
           for h, level in zip(headings, levels)]
    if prepended:
        dom.insert(0, Heading(level=1, text=fallback_title, anchor=None))

    md_lines = _markdown_heading_lines(chapter.markdown)
    if len(md_lines) != len(dom):
        _log.warning(
            "chapter %s: %d markdown headings vs %d in the DOM — dropping anchors",
            chapter.href, len(md_lines), len(dom),
        )
        # Still needs a level-1 entry at the top so the chapter owns its nodes.
        chapter.headings = [Heading(level=1, text=chapter.title, anchor=None)]
        chapter.heading_lines = [md_lines[0] if md_lines else 0]
        return

    chapter.headings = dom
    chapter.heading_lines = md_lines


def build_markdown(chapters: list[Chapter]) -> tuple[str, dict[int, tuple[Chapter, Heading]]]:
    """Concatenate chapters into one markdown document for md_to_tree, and map
    each heading's line in that document back to its chapter and anchor.

    The map is keyed on the same 1-based line numbers md_to_tree reports as
    `line_num`, which is how a tree node recovers its chapter.
    """
    lines: list[str] = []
    anchors: dict[int, tuple[Chapter, Heading]] = {}

    for chapter in chapters:
        offset = len(lines)  # 0-based index of this chapter's first line
        for local, heading in zip(chapter.heading_lines, chapter.headings):
            anchors[offset + local + 1] = (chapter, heading)  # +1 → 1-based
        lines.extend(chapter.markdown.split("\n"))
        lines.append("")

    return "\n".join(lines), anchors


# ── serve half ───────────────────────────────────────────────────────────────

def render_chapter_html(html: str, media_url_prefix: str) -> str:
    """Sanitize stored chapter HTML and point its images at the media route.

    Runs per request, so it is cheap to change: tighten the allowlist or move the
    image route without re-ingesting a single book.
    """
    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        src = img.get("src")
        if src and not src.startswith(("http://", "https://", "data:")):
            img["src"] = f"{media_url_prefix.rstrip('/')}/{src.lstrip('/')}"

    return nh3.clean(
        str(soup),
        tags=_ALLOWED_TAGS,
        attributes={k: set(v) for k, v in _ALLOWED_ATTRS.items()},
        link_rel="noopener noreferrer",
    )
