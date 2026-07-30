"""Response schemas for the JSON endpoints.

These models are the wire contract: FastAPI filters responses to the declared
fields (extra OpenSearch _source fields are dropped, not leaked) and publishes
them in the OpenAPI spec at /openapi.json. docs/api.md is the human-readable
mirror, and also covers the SSE /search stream, which OpenAPI cannot express.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class DocumentSummary(BaseModel):
    doc_id: str
    doc_name: str
    description: str = ""
    source_type: str = "pdf"
    collection: Optional[str] = None
    page_count: int = 0
    node_count: int = 0


class DocumentList(BaseModel):
    documents: list[DocumentSummary]


class TocNode(BaseModel):
    """One node of the reconstructed tree. doc_id is omitted — it's the
    path parameter. Page anchors are PDF; chapter/char anchors are EPUB."""

    node_id: str
    title: str = ""
    summary: Optional[str] = None
    parent_node_id: Optional[str] = None
    depth: int = 0
    sibling_order: int = 0
    start_page: Optional[int] = None
    end_page: Optional[int] = None
    chapter_href: Optional[str] = None
    heading_anchor: Optional[str] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    child_count: int = 0
    is_leaf: bool = True
    token_count: Optional[int] = None
    nodes: list["TocNode"] = Field(default_factory=list)


class Toc(BaseModel):
    doc_id: str
    doc_name: str
    # "pdf" | "epub" | "markdown" — the reader picks its renderer off this.
    source_type: str = "pdf"
    tree: list[TocNode]


class ContentRecord(BaseModel):
    """One page_content record: a physical page of a PDF node, or a whole
    EPUB/markdown chapter fragment (content_html set, page_number nominal)."""

    doc_id: str
    node_id: str
    collection: Optional[str] = None
    page_number: int = 1
    content: str = ""
    content_html: Optional[str] = None
    chapter_href: Optional[str] = None
    token_count: int = 0


class ContentResponse(BaseModel):
    doc_id: str
    records: list[ContentRecord]


class SearchErrorEvent(BaseModel):
    """Terminal SSE error event on /search. OpenAPI can't express SSE, so this
    model exists to enforce the shape in code; docs/api.md documents it.
    `message` is presentable copy; `detail` is the raw exception string for
    diagnostics. Clients should fall back to `message` for unknown codes."""

    type: Literal["error"] = "error"
    code: Literal[
        "llm_payment",
        "llm_auth",
        "llm_rate_limit",
        "llm_unavailable",
        "search_backend_unavailable",
        "internal",
    ]
    message: str
    detail: Optional[str] = None


class Health(BaseModel):
    """GET /health — the compose healthcheck target. Reports OpenSearch
    reachability only; no LLM-key check, since a keyless stack is healthy
    for browsing and reading."""

    status: Literal["ok"] = "ok"
    opensearch: bool = True
    index_prefix: str
