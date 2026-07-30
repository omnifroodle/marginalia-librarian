"""Ingest dry-run planning: the free pass that predicts what a paid ingest
run would do. No LLM, no index writes — safe to run anywhere, including the
containerized stack against a mounted vault."""

from __future__ import annotations

import pytest

from librarian.config import Config
from librarian.ingestion import tree_processor as tp
from librarian.ingestion.pipeline import (
    IngestionPipeline,
    derive_collection,
    discover_files,
)


class StubStore:
    def __init__(self, existing: set[str] | None = None):
        self.existing = existing or set()

    def document_exists(self, doc_id: str) -> bool:
        return doc_id in self.existing


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    (root / "icons" / "core").mkdir(parents=True)
    (root / "fate" / "core").mkdir(parents=True)
    (root / "icons" / "core" / "ICONS.pdf").write_bytes(b"%PDF-1.4 icons body")
    (root / "fate" / "core" / "fate.epub").write_bytes(b"PK fake epub body")
    (root / "notes.md").write_text("# loose notes at the root")
    # Files an ingest run must ignore:
    (root / "icons" / "core" / "._ICONS.pdf").write_bytes(b"AppleDouble junk")
    (root / "readme.txt").write_text("unsupported extension")
    return root


@pytest.fixture
def pipeline(tmp_path, vault):
    config = Config(
        {
            "models": {"tree_generation": "openai/test", "reasoning": "openai/test"},
            "opensearch": {"index_prefix": "plan_test"},
            "library": {"vault_root": str(vault)},
            "ingestion": {"reject_log_path": str(tmp_path / "reject_log.json")},
        }
    )
    p = IngestionPipeline(config)
    p._store = StubStore()
    return p


def doc_id_of(path) -> str:
    doc_id, _sha, _size = tp.file_identity(path)
    return doc_id


def test_discover_files_filters_and_sorts(vault):
    names = [f.name for f in discover_files(vault)]
    assert names == ["fate.epub", "ICONS.pdf", "notes.md"]  # sorted by full path


def test_derive_collection():
    from pathlib import Path

    root = Path("/vault")
    assert derive_collection(root / "icons" / "core" / "a.pdf", root) == "icons/core"
    assert derive_collection(root / "a.pdf", root) is None
    assert derive_collection(root / "a.pdf", None) is None


def test_plan_folder_fresh_vault_would_ingest_everything(pipeline, vault):
    plans = pipeline.plan_folder(vault)
    assert [p.action for p in plans] == ["ingest", "ingest", "ingest"]
    by_name = {p.file.name: p for p in plans}
    assert by_name["ICONS.pdf"].collection == "icons/core"
    assert by_name["fate.epub"].collection == "fate/core"
    assert by_name["notes.md"].collection is None
    assert all(len(p.doc_id) == 16 for p in plans)


def test_plan_skips_already_indexed_content(pipeline, vault):
    icons = vault / "icons" / "core" / "ICONS.pdf"
    pipeline._store = StubStore(existing={doc_id_of(icons)})
    plans = {p.file.name: p for p in pipeline.plan_folder(vault)}
    assert plans["ICONS.pdf"].action == "skip_exists"
    assert plans["fate.epub"].action == "ingest"


def test_plan_reports_reject_logged_files_with_the_reason(pipeline, vault):
    icons = vault / "icons" / "core" / "ICONS.pdf"
    pipeline._reject_log.record_failure(
        doc_id_of(icons), icons.name, icons, "PageIndex error: boom"
    )
    plans = {p.file.name: p for p in pipeline.plan_folder(vault)}
    assert plans["ICONS.pdf"].action == "skip_rejected"
    assert "boom" in plans["ICONS.pdf"].reason


def test_plan_file_without_root_has_no_collection(pipeline, vault):
    plan = pipeline.plan_file(vault / "icons" / "core" / "ICONS.pdf")
    assert plan.action == "ingest"
    assert plan.collection is None
