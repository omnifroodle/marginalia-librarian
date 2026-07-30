"""API adapter tests: endpoints against an in-memory OpenSearch stub, and the
SSE /search stream against a stubbed orchestrator (no LLM, no network)."""

from __future__ import annotations

import json
import zipfile

import pytest

from librarian.config import Config

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from librarian.api import create_app  # noqa: E402
from librarian.query.orchestrator import SearchResult  # noqa: E402

PREFIX = "test"


class FakeOpenSearch:
    """Covers only the client surface the API endpoints touch."""

    def __init__(self, documents=None, tree_nodes=None, pages=None):
        self.documents = documents or []
        self.tree_nodes = tree_nodes or []
        self.pages = pages or []
        self.reachable = True

    def ping(self):
        if isinstance(self.reachable, Exception):
            raise self.reachable
        return self.reachable

    def get(self, index, id):
        for d in self.documents:
            if d["doc_id"] == id:
                return {"_source": d}
        raise KeyError(id)

    def search(self, index=None, body=None, routing=None, params=None):
        if index == f"{PREFIX}_documents":
            hits = self.documents
        elif index == f"{PREFIX}_tree_nodes":
            hits = [n for n in self.tree_nodes if n["doc_id"] == routing]
        elif index == f"{PREFIX}_page_content":
            hits = self.pages
            for f in body["query"]["bool"]["filter"]:
                if "term" in f:
                    ((field, value),) = f["term"].items()
                    hits = [h for h in hits if h.get(field) == value]
                elif "terms" in f:
                    ((field, values),) = f["terms"].items()
                    hits = [h for h in hits if h.get(field) in values]
        else:
            hits = []
        return {"hits": {"hits": [{"_source": dict(h), "_score": 1.0} for h in hits]}}


class FakeOrchestrator:
    """Emits a canned event sequence, then returns a small SearchResult."""

    def run(self, question, *, top_k=None, doc_id=None, on_event=None):
        on_event({"type": "phase_started", "phase": 1, "name": "discovery", "msg": "…"})
        on_event({"type": "candidates", "items": [{"doc_id": "d1", "doc_name": "Book"}]})
        on_event({"type": "citation_ready", "rank": 0, "citation": {"doc_id": "d1"}})
        return SearchResult(
            question=question,
            candidates=[{"doc_id": "d1", "doc_name": "Book", "score": 1.0}],
            shortlisted_doc_ids=["d1"],
        )


class ExplodingOrchestrator:
    def run(self, question, *, top_k=None, doc_id=None, on_event=None):
        raise RuntimeError("opensearch is down")


FAKE_PNG = b"\x89PNG\r\n\x1a\n fake image bytes"


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "book.pdf").write_bytes(b"%PDF-1.4 fake pdf body for range tests")

    # A real archive, so the media route's namelist guard is exercised for real.
    with zipfile.ZipFile(tmp_path / "graphdb.epub", "w") as archive:
        archive.writestr("OEBPS/ch01.html", "<h1>Chapter</h1>")
        archive.writestr("OEBPS/assets/fig01.png", FAKE_PNG)
    return tmp_path


@pytest.fixture
def client(vault):
    config = Config(
        {
            "models": {"tree_generation": "openai/test", "reasoning": "openai/test"},
            "opensearch": {"index_prefix": PREFIX},
            "library": {"vault_root": str(vault)},
        }
    )
    fake = FakeOpenSearch(
        documents=[
            {
                "doc_id": "d1",
                "doc_name": "Book",
                "source_path": "book.pdf",
                "source_type": "pdf",
            },
            {"doc_id": "d2", "doc_name": "No Source"},
            {"doc_id": "d3", "doc_name": "Evil", "source_path": "../outside.pdf"},
            {
                "doc_id": "d4",
                "doc_name": "Graph Databases",
                "source_path": "graphdb.epub",
                "source_type": "epub",
            },
        ],
        tree_nodes=[
            {"doc_id": "d1", "node_id": "001", "parent_node_id": None,
             "depth": 0, "sibling_order": 0, "title": "Root"},
            {"doc_id": "d1", "node_id": "001.001", "parent_node_id": "001",
             "depth": 1, "sibling_order": 0, "title": "Child"},
        ],
        pages=[
            {"doc_id": "d1", "node_id": "001", "page_number": 1,
             "content": "page one", "content_search": "page one"},
            {"doc_id": "d1", "node_id": "001", "page_number": 2,
             "content": "page two", "content_search": "page two"},
            {"doc_id": "d1", "node_id": "002", "page_number": 5,
             "content": "chapter text", "content_search": "chapter text",
             "chapter_href": "ch1.xhtml"},
            # An EPUB chapter's HTML record: no text, addressable only by chapter.
            {"doc_id": "d4", "node_id": "chapter:1", "page_number": 1,
             "content": "", "content_search": "", "chapter_href": "ch01.html",
             "content_html": (
                 '<div id="chapter1"><h1>Chapter</h1>'
                 '<script>alert(1)</script>'
                 '<p onclick="evil()">Graphs.</p>'
                 '<img src="OEBPS/assets/fig01.png" alt="fig"/></div>'
             )},
        ],
    )
    app = create_app(config, client=fake)
    app.state.orchestrator = FakeOrchestrator()
    return TestClient(app)


def _sse_events(text: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


# ── /search ───────────────────────────────────────────────────────────────────

def test_search_streams_events_then_done(client):
    resp = client.post("/search", json={"question": "what are power stunts?"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _sse_events(resp.text)
    types = [e["type"] for e in events]
    assert types == ["phase_started", "candidates", "citation_ready", "done"]
    assert events[-1]["result"]["question"] == "what are power stunts?"
    assert events[-1]["result"]["shortlisted_doc_ids"] == ["d1"]


def test_search_error_becomes_terminal_event(client):
    client.app.state.orchestrator = ExplodingOrchestrator()
    resp = client.post("/search", json={"question": "boom"})
    assert resp.status_code == 200  # stream opens before the failure
    events = _sse_events(resp.text)
    assert events == [
        {
            "type": "error",
            "code": "internal",
            "message": "Something went wrong inside the librarian.",
            "detail": "opensearch is down",
        }
    ]


def test_search_error_code_reflects_the_failure_class(client):
    """The terminal event's code comes from classify_error, wired for real."""
    from opensearchpy.exceptions import ConnectionError as OSConnectionError

    class DownOrchestrator:
        def run(self, question, *, top_k=None, doc_id=None, on_event=None):
            raise OSConnectionError("N/A", "connection refused", None)

    client.app.state.orchestrator = DownOrchestrator()
    resp = client.post("/search", json={"question": "boom"})
    (event,) = _sse_events(resp.text)
    assert event["code"] == "search_backend_unavailable"
    assert event["message"] == "The search index is unreachable."


def test_health_ok_when_opensearch_pings(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "opensearch": True,
        "index_prefix": PREFIX,
    }


def test_health_503_when_opensearch_unreachable(vault):
    config = Config(
        {
            "models": {"tree_generation": "openai/test", "reasoning": "openai/test"},
            "opensearch": {"index_prefix": PREFIX},
            "library": {"vault_root": str(vault)},
        }
    )
    fake = FakeOpenSearch()
    fake.reachable = ConnectionError("nobody home")
    resp = TestClient(create_app(config, client=fake)).get("/health")
    assert resp.status_code == 503


def test_search_rejects_empty_question(client):
    assert client.post("/search", json={"question": ""}).status_code == 422
    assert client.post("/search", json={}).status_code == 422


# ── /documents, /toc, /content ────────────────────────────────────────────────

def test_documents_lists_all(client):
    docs = client.get("/documents").json()["documents"]
    assert [d["doc_id"] for d in docs] == ["d1", "d2", "d3", "d4"]
    # response_model filters index fields that aren't part of the contract
    assert all("source_path" not in d for d in docs)


def test_openapi_declares_response_schemas(client):
    spec = client.get("/openapi.json").json()
    for path, schema in [
        ("/documents", "DocumentList"),
        ("/toc/{doc_id}", "Toc"),
        ("/content/{doc_id}", "ContentResponse"),
    ]:
        ref = spec["paths"][path]["get"]["responses"]["200"]["content"][
            "application/json"]["schema"]["$ref"]
        assert ref == f"#/components/schemas/{schema}"


def test_toc_reconstructs_tree(client):
    body = client.get("/toc/d1").json()
    assert body["doc_name"] == "Book"
    assert len(body["tree"]) == 1
    root = body["tree"][0]
    assert root["node_id"] == "001"
    assert [c["node_id"] for c in root["nodes"]] == ["001.001"]


def test_toc_unknown_doc_is_404(client):
    assert client.get("/toc/nope").status_code == 404


def test_content_by_node_id(client):
    body = client.get("/content/d1", params={"node_id": "001"}).json()
    assert [r["page_number"] for r in body["records"]] == [1, 2]
    assert all("content_search" not in r for r in body["records"])


def test_content_by_chapter(client):
    body = client.get("/content/d1", params={"chapter": "ch1.xhtml"}).json()
    assert len(body["records"]) == 1
    assert body["records"][0]["content"] == "chapter text"


def test_toc_reports_source_type_so_the_reader_can_pick_a_renderer(client):
    assert client.get("/toc/d1").json()["source_type"] == "pdf"


def test_chapter_html_is_sanitized_on_the_way_out(client):
    body = client.get("/content/d4", params={"chapter": "ch01.html"}).json()
    html = body["records"][0]["content_html"]

    assert "<script" not in html and "alert(1)" not in html
    assert "onclick" not in html
    assert "Graphs." in html
    assert 'id="chapter1"' in html  # heading anchors survive — citations need them


def test_chapter_images_are_repointed_at_the_media_route(client):
    body = client.get("/content/d4", params={"chapter": "ch01.html"}).json()

    assert 'src="/assets/d4/media/OEBPS/assets/fig01.png"' in body["records"][0]["content_html"]


def test_media_serves_an_entry_out_of_the_epub(client):
    resp = client.get("/assets/d4/media/OEBPS/assets/fig01.png")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == FAKE_PNG


def test_media_404s_for_a_path_not_in_the_archive(client):
    """Membership in the archive's namelist IS the traversal guard — a path
    that is not an entry has nothing to escape into."""
    assert client.get("/assets/d4/media/OEBPS/../../etc/passwd").status_code == 404
    assert client.get("/assets/d4/media/nope.png").status_code == 404


def test_media_404s_for_a_document_with_no_archive(client):
    assert client.get("/assets/d1/media/anything.png").status_code == 404


def test_content_requires_a_selector(client):
    assert client.get("/content/d1").status_code == 400


def test_content_missing_is_404(client):
    assert client.get("/content/d1", params={"node_id": "zzz"}).status_code == 404


# ── /assets ───────────────────────────────────────────────────────────────────

def test_assets_serves_full_file(client, vault):
    resp = client.get("/assets/d1")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content == (vault / "book.pdf").read_bytes()


def test_assets_supports_range_requests(client, vault):
    resp = client.get("/assets/d1", headers={"Range": "bytes=0-7"})
    assert resp.status_code == 206
    assert resp.content == (vault / "book.pdf").read_bytes()[:8]
    assert "content-range" in resp.headers


def test_assets_unknown_doc_is_404(client):
    assert client.get("/assets/nope").status_code == 404


def test_assets_without_source_path_is_404(client):
    assert client.get("/assets/d2").status_code == 404


def test_assets_blocks_path_traversal(client, vault):
    # d3's source_path points outside the vault; make the target real so the
    # test proves the guard (not just a missing-file 404).
    (vault.parent / "outside.pdf").write_bytes(b"secret")
    assert client.get("/assets/d3").status_code == 403
