"""Pydantic models for the three OpenSearch indexes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class DocumentRecord(BaseModel):
    """Maps to the 'documents' index — one per source file."""

    doc_id: str
    doc_name: str
    source_type: str = "pdf"
    # Path to the original file relative to the configured vault_root, so the
    # reader can serve the source PDF/EPUB. Never absolute.
    source_path: Optional[str] = None
    file_sha256: Optional[str] = None
    file_size: Optional[int] = None
    domain: Optional[str] = None
    collection: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    page_count: int = 0
    language: str = "en"
    tags: list[str] = Field(default_factory=list)

    # Full-text and semantic discovery fields
    description: str = ""
    description_embedding: Optional[list[float]] = None
    root_summary: str = ""
    root_summary_embedding: Optional[list[float]] = None
    top_level_titles: str = ""

    # Tree metadata
    tree_depth: int = 0
    node_count: int = 0

    # Model provenance
    embedding_model: Optional[str] = None
    summary_model: Optional[str] = None

    def to_os_doc(self) -> dict:
        """Serialize for OpenSearch, converting datetimes to ISO strings."""
        d = self.model_dump()
        d["created_at"] = self.created_at.isoformat()
        d["updated_at"] = self.updated_at.isoformat()
        return d


class TreeNodeRecord(BaseModel):
    """Maps to the 'tree_nodes' index — one per node in the document tree."""

    doc_id: str
    node_id: str
    collection: Optional[str] = None
    parent_node_id: Optional[str] = None
    depth: int = 0
    sibling_order: int = 0

    title: str = ""
    summary: Optional[str] = None

    # Page ranges (1-based, from PageIndex start_index/end_index). PDF only;
    # EPUB/markdown nodes anchor via the chapter fields below instead.
    start_page: int = 1
    end_page: int = 1

    # EPUB/markdown anchors (null for PDF)
    chapter_href: Optional[str] = None
    heading_anchor: Optional[str] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None

    token_count: Optional[int] = None
    child_count: int = 0
    is_leaf: bool = True

    # Model provenance
    summary_model: Optional[str] = None

    def to_os_doc(self) -> dict:
        return self.model_dump(exclude_none=True)


class PageContentRecord(BaseModel):
    """Maps to the 'page_content' index.

    PDF: one record per (doc_id, node_id, physical page) — split on the
    <physical_index_N> markers retained at ingestion, so quotes resolve to a page.
    EPUB/markdown: one record per node, with the rendered chapter fragment in
    content_html and the chapter href for anchoring.
    """

    doc_id: str
    node_id: str
    collection: Optional[str] = None
    page_number: int = 1
    content: str = ""
    content_html: Optional[str] = None
    chapter_href: Optional[str] = None
    token_count: int = 0

    def to_os_doc(self) -> dict:
        d = self.model_dump(exclude_none=True)
        # content_search mirrors content but is indexed for BM25 search_pages queries
        d["content_search"] = self.content
        return d
