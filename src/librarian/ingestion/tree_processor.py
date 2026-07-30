"""Wrap PageIndex and flatten its tree output into OpenSearch records."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ..config import Config
from ..models import DocumentRecord, PageContentRecord, TreeNodeRecord

# File types process_file knows how to turn into a tree.
SUPPORTED_EXTENSIONS = {".pdf", ".md", ".markdown", ".epub"}

# Matches one page block produced by add_node_text_with_labels:
# <physical_index_N>\n{page text}\n<physical_index_N>\n  (same marker opens and closes)
_PAGE_BLOCK = re.compile(
    r"<physical_index_(\d+)>\n(.*?)\n<physical_index_\1>", re.DOTALL
)


def file_identity(file_path: Path) -> tuple[str, str, int]:
    """Return (doc_id, file_sha256, file_size) from the file's content.

    Content-hash doc_ids are rename-stable and dedupe identical files
    ingested from different paths.
    """
    h = hashlib.sha256()
    size = 0
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
            size += len(chunk)
    sha = h.hexdigest()
    return sha[:16], sha, size


def relative_source_path(file_path: Path, config: Config) -> str:
    """Path stored in the index: relative to vault_root when the file is under
    it, else the bare filename (still resolvable if the file is later moved
    into the vault)."""
    try:
        return str(file_path.resolve().relative_to(config.vault_root.resolve()))
    except ValueError:
        return file_path.name


def process_file(file_path: Path, config: Config) -> dict:
    """Call PageIndex on a PDF/Markdown/EPUB file and return its raw output dict.

    Returns a dict with keys: doc_name, doc_description (optional), structure.
    Caller is responsible for having called pageindex.configure_llm() first
    (IngestionPipeline does this).
    """
    suffix = file_path.suffix.lower()

    if suffix in (".md", ".markdown"):
        return _process_markdown(file_path, config)
    elif suffix == ".pdf":
        return _process_pdf(file_path, config)
    elif suffix == ".epub":
        return _process_epub(file_path, config)
    else:
        raise ValueError(f"Unsupported file type: {suffix}. Supported: .pdf, .md, .markdown, .epub")


def _process_pdf(file_path: Path, config: Config) -> dict:
    from ..pageindex import page_index

    result = page_index(
        str(file_path),
        model=config.tree_generation_model,
        if_add_node_id="yes",
        if_add_node_summary="yes",
        if_add_doc_description="yes",
        if_add_node_text="yes",
        # Interleave <physical_index_N> markers so flatten_tree can split node
        # text into per-page records (the anchoring unit for liner notes).
        if_add_node_text_labels="yes",
    )
    return result


def _process_markdown(file_path: Path, config: Config) -> dict:
    import asyncio

    from ..pageindex import md_to_tree

    result = asyncio.run(md_to_tree(
        str(file_path),
        model=config.tree_generation_model,
        if_add_node_summary="yes",
        summary_token_threshold=200,
        if_add_doc_description="yes",
        if_add_node_text="yes",
        if_add_node_id="yes",
    ))
    structure = result.get("structure", [])
    if not isinstance(structure, list):
        structure = [structure]

    return {
        "doc_name": result.get("doc_name") or file_path.stem,
        "doc_description": result.get("doc_description"),
        "structure": structure,
    }


def _process_epub(file_path: Path, config: Config) -> dict:
    """Chapter-wise EPUB → Markdown → tree, keeping each node's chapter.

    The chapter/heading/anchor work lives in ../epub.py as a pure function, so
    it is testable without an LLM. Here we only run the tree over the assembled
    markdown and stitch each node back to the chapter its heading came from.
    """
    import asyncio
    import os
    import tempfile

    from ..epub import build_markdown, extract_chapters
    from ..pageindex import md_to_tree

    doc_name, chapters = extract_chapters(file_path)
    if not chapters:
        raise ValueError(f"No readable chapters found in {file_path.name}")

    full_markdown, anchors = build_markdown(chapters)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", encoding="utf-8", delete=False
    ) as tmp:
        tmp.write(full_markdown)
        tmp_path = tmp.name

    try:
        result = asyncio.run(md_to_tree(
            tmp_path,
            model=config.tree_generation_model,
            if_add_node_summary="yes",
            summary_token_threshold=200,
            if_add_doc_description="yes",
            if_add_node_text="yes",
            if_add_node_id="yes",
        ))
    finally:
        os.unlink(tmp_path)

    structure = result.get("structure", [])
    if not isinstance(structure, list):
        structure = [structure]

    _assign_chapters(structure, anchors, fallback=chapters[0])

    return {
        "doc_name": doc_name,
        "doc_description": result.get("doc_description"),
        "structure": structure,
        "chapters": [
            {"index": c.index, "href": c.href, "title": c.title, "html": c.html}
            for c in chapters
        ],
    }


def _assign_chapters(nodes: list[dict], anchors: dict, fallback) -> None:
    """Stamp each tree node with the chapter its heading lives in.

    md_to_tree reports the source `line_num` of every node; `anchors` maps those
    same line numbers to (chapter, heading). Because every chapter begins with a
    level-1 heading, a node never straddles a chapter boundary — so start_index
    and end_index are both just the chapter's spine position.
    """
    for node in nodes:
        chapter, heading = anchors.get(node.get("line_num"), (fallback, None))
        node["start_index"] = chapter.index
        node["end_index"] = chapter.index
        node["chapter_href"] = chapter.href
        if heading and heading.anchor:
            node["heading_anchor"] = heading.anchor
        if node.get("nodes"):
            _assign_chapters(node["nodes"], anchors, fallback)


# ── tree flattening ───────────────────────────────────────────────────────────

def split_labeled_text(text: str, fallback_page: int = 1) -> list[tuple[int, str]]:
    """Split node text containing <physical_index_N> markers into
    (page_number, page_text) pairs.

    Falls back to a single block at fallback_page when no markers are present
    (markdown/EPUB nodes, or PDFs ingested without labels).
    """
    blocks = [(int(m.group(1)), m.group(2)) for m in _PAGE_BLOCK.finditer(text)]
    if not blocks:
        stripped = text.strip()
        return [(fallback_page, stripped)] if stripped else []
    return blocks


def strip_page_labels(text: str) -> str:
    """Remove <physical_index_N> marker lines, keeping the raw text."""
    return re.sub(r"<physical_index_\d+>\n?", "", text)


def flatten_tree(
    raw_output: dict,
    file_path: Path,
    config: Config,
    doc_id: str,
    file_sha256: str | None = None,
    file_size: int | None = None,
    collection: str | None = None,
    summary_model: str | None = None,
) -> tuple[DocumentRecord, list[TreeNodeRecord], list[PageContentRecord]]:
    """Transform PageIndex output into records for all three indexes."""
    structure: list[dict] = raw_output.get("structure", [])
    doc_description: str = raw_output.get("doc_description") or ""
    raw_name: str = raw_output.get("doc_name") or file_path.stem
    # PageIndex returns the filename with extension — strip it for a clean
    # doc_name. Only a real extension, though: EPUBs supply their Dublin Core
    # title here, and "Graph Databases: Vol. 2" must not become "Graph Databases: Vol".
    doc_name: str = (
        Path(raw_name).stem
        if Path(raw_name).suffix.lower() in SUPPORTED_EXTENSIONS
        else raw_name
    )

    tree_nodes: list[TreeNodeRecord] = []
    page_content_records: list[PageContentRecord] = []

    def walk(
        nodes: list[dict],
        parent_id: str | None,
        depth: int,
    ) -> None:
        for order, node in enumerate(nodes):
            node_id: str = node.get("node_id") or f"{depth}_{order}"
            children: list[dict] = node.get("nodes") or []
            is_leaf = len(children) == 0

            tree_node = TreeNodeRecord(
                doc_id=doc_id,
                node_id=node_id,
                collection=collection,
                parent_node_id=parent_id,
                depth=depth,
                sibling_order=order,
                title=node.get("title", ""),
                summary=_clean_summary(node.get("summary")),
                # PageIndex uses start_index/end_index (1-based page numbers);
                # for EPUB these are the chapter's spine position.
                start_page=node.get("start_index", 1),
                end_page=node.get("end_index", 1),
                chapter_href=node.get("chapter_href"),
                heading_anchor=node.get("heading_anchor"),
                child_count=len(children),
                is_leaf=is_leaf,
                summary_model=summary_model,
            )
            tree_nodes.append(tree_node)

            # One page_content record per physical page (PDF) or per node
            # (markdown/EPUB) — page-level records are what liner-note quotes
            # resolve against.
            text: str = node.get("text") or ""
            if text.strip():
                for page_number, page_text in split_labeled_text(
                    text, fallback_page=node.get("start_index", 1)
                ):
                    if not page_text.strip():
                        continue
                    page_content_records.append(
                        PageContentRecord(
                            doc_id=doc_id,
                            node_id=node_id,
                            collection=collection,
                            page_number=page_number,
                            content=page_text,
                            chapter_href=node.get("chapter_href"),
                            token_count=_rough_token_count(page_text),
                        )
                    )

            if children:
                walk(children, parent_id=node_id, depth=depth + 1)

    walk(structure, parent_id=None, depth=0)

    page_content_records.extend(
        _chapter_html_records(raw_output.get("chapters") or [], doc_id, collection)
    )

    # Compute tree stats
    tree_depth = max((n.depth for n in tree_nodes), default=0)
    node_count = len(tree_nodes)

    # Top-level section titles for BM25 discovery
    top_level_titles = " | ".join(
        n.title for n in tree_nodes if n.depth == 0 and n.title
    )

    # Root summary: the summary of the first top-level node (or first node found)
    root_summary = ""
    if tree_nodes:
        root_nodes = [n for n in tree_nodes if n.depth == 0]
        if root_nodes and root_nodes[0].summary:
            root_summary = root_nodes[0].summary
        elif tree_nodes[0].summary:
            root_summary = tree_nodes[0].summary

    # Page count: max end_page seen across all nodes
    page_count = max((n.end_page for n in tree_nodes), default=0)

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    doc_record = DocumentRecord(
        doc_id=doc_id,
        doc_name=doc_name,
        source_type={
            ".md": "markdown",
            ".markdown": "markdown",
            ".epub": "epub",
        }.get(file_path.suffix.lower(), "pdf"),
        source_path=relative_source_path(file_path, config),
        file_sha256=file_sha256,
        file_size=file_size,
        collection=collection,
        created_at=now,
        updated_at=now,
        page_count=page_count,
        description=doc_description,
        root_summary=root_summary,
        top_level_titles=top_level_titles,
        tree_depth=tree_depth,
        node_count=node_count,
        summary_model=summary_model,
    )

    return doc_record, tree_nodes, page_content_records


def _chapter_html_records(
    chapters: list[dict], doc_id: str, collection: str | None
) -> list[PageContentRecord]:
    """One record per EPUB chapter holding the renderable HTML.

    Deliberately *not* per node: a chapter with six headings would otherwise
    store the same HTML six times. Three properties make this safe to park in
    the same index as the text records:

    - `content` is empty, so `content_search` analyzes to zero tokens and the
      record can never match a BM25 `search_pages` query.
    - `node_id` ("chapter:3") is never a real tree node, so the note generator's
      `get_page_content(doc_id, node_ids)` never returns it — the quote resolver
      never sees a record with no text to match against.
    - the `_id` (doc_id::chapter:3::3) can't collide with a node's record.

    Addressable only by `chapter_href`, which is exactly how the reader asks.
    """
    return [
        PageContentRecord(
            doc_id=doc_id,
            node_id=f"chapter:{chapter['index']}",
            collection=collection,
            page_number=chapter["index"],
            content="",
            content_html=chapter["html"],
            chapter_href=chapter["href"],
        )
        for chapter in chapters
        if chapter.get("html")
    ]


def _clean_summary(summary: str | None) -> str | None:
    """Node summaries are generated over labeled text; scrub any markers the
    model echoed back."""
    if not summary:
        return summary
    return re.sub(r"<physical_index_\d+>", "", summary).strip() or None


def _rough_token_count(text: str) -> int:
    """Rough token estimate (4 chars ≈ 1 token)."""
    return max(1, len(text) // 4)
