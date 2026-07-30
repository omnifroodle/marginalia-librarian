"""FastAPI adapter over the librarian library.

Thin by design (see docs/design.md seam rules): endpoints wire config, call
into query/ and opensearch/, and shape HTTP responses. The /search endpoint
bridges the orchestrator's synchronous on_event callback into an SSE stream
with the queue + worker-thread + generator pattern.

Requires the [api] extra: pip install 'librarian[api]'.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import zipfile
from pathlib import Path, PurePosixPath
from typing import Optional

from pydantic import BaseModel, Field

from ..config import Config
from ..epub import render_chapter_html
from .errors import classify_error
from .schemas import ContentResponse, DocumentList, Health, SearchErrorEvent, Toc

_log = logging.getLogger("librarian.api")

_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".epub": "application/epub+zip",
    ".md": "text/markdown",
    # Media served out of an EPUB archive (figures, and the odd inline font/CSS)
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".css": "text/css",
    ".otf": "font/otf",
    ".ttf": "font/ttf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}

# Seconds between SSE keepalive comments while a search phase is quiet.
_KEEPALIVE_INTERVAL = 15


class SearchRequest(BaseModel):
    question: str = Field(min_length=1)
    doc_id: Optional[str] = None
    top_k: Optional[int] = Field(default=None, ge=1)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def create_app(config: Config, client=None):
    """Build the FastAPI app. Pass an OpenSearch client to share/stub one;
    otherwise a client is created from config."""
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import FileResponse, Response, StreamingResponse
    except ImportError as e:
        raise ImportError(
            "FastAPI not installed. Run: pip install 'librarian[api]'"
        ) from e

    from ..opensearch.mappings import IndexNames
    from ..opensearch.store import DocumentStore
    from ..query.orchestrator import QueryOrchestrator
    from ..query.search import DocumentSearcher

    if client is None:
        from ..opensearch.client import create_client
        client = create_client(config)

    app = FastAPI(title="librarian", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.api_cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    indexes = IndexNames.from_config(config)
    # Dependencies live on app.state so tests can swap them per-instance.
    app.state.config = config
    app.state.store = DocumentStore(client, indexes)
    app.state.searcher = DocumentSearcher(client, indexes)
    app.state.orchestrator = QueryOrchestrator(config, client=client)

    @app.post("/search")
    def search(req: SearchRequest):
        """SSE stream of orchestrator events (phase_started, candidates,
        docs_shortlisted, tool_call, selections, citation_ready), then a
        terminal done event carrying the full SearchResult, or error."""
        events: queue.Queue = queue.Queue()

        def run() -> None:
            try:
                result = app.state.orchestrator.run(
                    req.question,
                    top_k=req.top_k,
                    doc_id=req.doc_id,
                    on_event=events.put,
                )
                events.put({"type": "done", "result": result.to_dict()})
            except Exception as e:
                _log.exception("search failed: %s", req.question)
                code, message = classify_error(e)
                events.put(
                    SearchErrorEvent(
                        code=code, message=message, detail=str(e)
                    ).model_dump()
                )
            finally:
                events.put(None)  # sentinel

        threading.Thread(target=run, daemon=True).start()

        def stream():
            # The worker thread is not cancelled on client disconnect; the
            # funnel runs to completion server-side and events are dropped.
            while True:
                try:
                    event = events.get(timeout=_KEEPALIVE_INTERVAL)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                if event is None:
                    break
                yield _sse(event)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/health", response_model=Health)
    def health() -> dict:
        """Liveness + OpenSearch reachability. Deliberately no LLM-key check:
        a keyless stack still serves browsing and reading."""
        try:
            reachable = bool(client.ping())
        except Exception:
            reachable = False
        if not reachable:
            raise HTTPException(503, "opensearch unreachable")
        return {"opensearch": True, "index_prefix": config.index_prefix}

    @app.get("/documents", response_model=DocumentList)
    def documents() -> dict:
        return {"documents": app.state.store.list_documents()}

    @app.get("/toc/{doc_id}", response_model=Toc)
    def toc(doc_id: str) -> dict:
        flat = app.state.searcher.get_tree_nodes(doc_id)
        if not flat:
            raise HTTPException(404, f"No tree found for doc_id: {doc_id}")
        doc = app.state.store.get_document(doc_id) or {}
        return {
            "doc_id": doc_id,
            "doc_name": doc.get("doc_name", doc_id),
            # The reader picks its renderer off this (pdf.js vs chapter HTML).
            "source_type": doc.get("source_type", "pdf"),
            "tree": app.state.searcher.reconstruct_tree(flat),
        }

    @app.get("/content/{doc_id}", response_model=ContentResponse)
    def content(
        doc_id: str,
        node_id: Optional[str] = None,
        chapter: Optional[str] = None,
    ) -> dict:
        """Page/chapter records for a document: ?node_id= returns a node's
        page records (PDF), ?chapter= returns an EPUB chapter's records.

        Chapter HTML is sanitized and its images repointed at /assets/../media
        on the way out — never at ingest, so tightening the allowlist or moving
        the media route costs a request, not a re-ingest.
        """
        if not node_id and not chapter:
            raise HTTPException(400, "Provide a node_id or chapter query parameter")
        if chapter:
            records = [
                dict(r) for r in app.state.store.get_chapter_content(doc_id, chapter)
            ]
            for record in records:
                if record.get("content_html"):
                    record["content_html"] = render_chapter_html(
                        record["content_html"], f"/assets/{doc_id}/media"
                    )
        else:
            records = app.state.store.get_page_content(doc_id, [node_id])
        if not records:
            raise HTTPException(404, f"No content found for doc_id: {doc_id}")
        return {"doc_id": doc_id, "records": records}

    def _source_file(doc_id: str) -> Path:
        doc = app.state.store.get_document(doc_id)
        if not doc:
            raise HTTPException(404, f"No document with doc_id: {doc_id}")
        source_path = doc.get("source_path")
        if not source_path:
            raise HTTPException(404, f"Document has no source_path: {doc_id}")

        vault = app.state.config.vault_root.resolve()
        path = (vault / source_path).resolve()
        if not path.is_relative_to(vault):
            raise HTTPException(403, "source_path escapes vault_root")
        if not path.is_file():
            raise HTTPException(404, f"Source file missing from vault: {source_path}")
        return path

    @app.get("/assets/{doc_id}")
    def assets(doc_id: str):
        """Stream the original source file from the vault. FileResponse
        handles HTTP Range requests (pdf.js fetches PDFs in chunks)."""
        path = _source_file(doc_id)
        return FileResponse(
            path,
            media_type=_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
            filename=path.name,
        )

    @app.get("/assets/{doc_id}/media/{media_path:path}")
    def media(doc_id: str, media_path: str):
        """Serve one entry out of an EPUB archive — the images its chapters
        reference.

        The traversal guard is membership in the archive's namelist: a path that
        is not an entry is a 404, so `..` has nothing to escape into. The
        filesystem guard on /assets/{doc_id} does not apply here — that one
        protects a source_path the database controls, whereas this path comes
        from the caller.
        """
        path = _source_file(doc_id)
        if path.suffix.lower() != ".epub":
            raise HTTPException(404, "Document has no embedded media")

        try:
            with zipfile.ZipFile(path) as archive:
                if media_path not in archive.namelist():
                    raise HTTPException(404, f"No such entry in {path.name}: {media_path}")
                data = archive.read(media_path)
        except zipfile.BadZipFile:
            raise HTTPException(500, f"Unreadable EPUB: {path.name}")

        suffix = PurePosixPath(media_path).suffix.lower()
        return Response(
            content=data,
            media_type=_MEDIA_TYPES.get(suffix, "application/octet-stream"),
            headers={"Cache-Control": "public, max-age=86400"},
        )

    return app
