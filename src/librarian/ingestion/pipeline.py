"""Orchestrate the full ingestion flow for one or many documents.

Library code: no console output. Progress is reported through an optional
callback; per-file outcomes come back as IngestResult records for the caller
(CLI, API) to present.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ..config import Config
from ..ingestion import embedding as emb
from ..ingestion import tree_processor as tp
from ..ingestion.reject_log import RejectLog
from ..pageindex import LLMRateLimitExhausted, RetryPolicy, configure_llm

_log = logging.getLogger("librarian.ingestion")

# Defined next to the dispatch that uses it; re-exported here for callers.
SUPPORTED_EXTENSIONS = tp.SUPPORTED_EXTENSIONS

# Progress callback: (file_path, stage) where stage is one of
# "parsing" | "flattening" | "embedding" | "storing"
ProgressFn = Callable[[Path, str], None]


@dataclass
class IngestResult:
    file: Path
    status: str  # "ok" | "skipped" | "rejected" | "error"
    error: Optional[str] = None
    node_count: int = 0
    page_record_count: int = 0
    elapsed_seconds: float = 0.0
    rate_limited: bool = False  # provider 429s exhausted retries; file not reject-logged


@dataclass
class IngestPlan:
    """What ingesting one file would do — the dry-run unit. Produced without
    any LLM call or index write."""

    file: Path
    doc_id: str
    collection: Optional[str]
    action: str  # "ingest" | "skip_exists" | "skip_rejected"
    reason: Optional[str] = None


def discover_files(folder: Path) -> list[Path]:
    """The one place that decides which files an ingest run covers."""
    return sorted(
        f
        for f in folder.rglob("*")
        if f.is_file()
        and f.suffix.lower() in SUPPORTED_EXTENSIONS
        and not f.name.startswith("._")  # AppleDouble metadata
    )


def derive_collection(file_path: Path, root_folder: Path | None) -> Optional[str]:
    """Collection = the file's subdirectory path relative to the ingestion
    root: root/D&D 5e/PHB.pdf → "D&D 5e", root/pathfinder/core/CRB.pdf →
    "pathfinder/core". Files at the root (or single-file ingests) have none."""
    if root_folder is None:
        return None
    rel = file_path.parent.relative_to(root_folder)
    return None if str(rel) == "." else str(rel)


class IngestionPipeline:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._reject_log = RejectLog(config.reject_log_path)

        configure_llm(
            api_base=config.litellm_api_base,
            api_key=config.litellm_api_key,
            max_concurrent=config.max_concurrent_llm_calls,
            log_dir=str(config.log_dir) if config.log_dir else None,
            retry_policy=RetryPolicy(
                num_retries=config.num_retries,
                rate_limit_retries=config.rate_limit_retries,
                rate_limit_max_wait=float(config.rate_limit_max_wait),
                timeout=config.completion_timeout,
            ),
        )

        from ..backends import StoreProtocol
        from ..opensearch.client import create_client
        from ..opensearch.mappings import IndexNames
        from ..opensearch.store import DocumentStore

        self._store: StoreProtocol = DocumentStore(
            create_client(config), IndexNames.from_config(config)
        )

    def ingest_folder(
        self, folder: Path, force: bool = False, on_progress: ProgressFn | None = None
    ) -> list[IngestResult]:
        files = discover_files(folder)
        results: list[IngestResult] = []
        for i, f in enumerate(files):
            result = self._ingest_one(f, force=force, root_folder=folder, on_progress=on_progress)
            results.append(result)
            if result.rate_limited:
                # The host is hard rate limiting; each further file would burn
                # the full retry budget for nothing. Stop and let the user re-run.
                _log.error(
                    "aborting ingest run: provider is rate limiting (%d files unprocessed)",
                    len(files) - i - 1,
                )
                break
        return results

    def ingest_file(
        self, file_path: Path, force: bool = False, on_progress: ProgressFn | None = None
    ) -> IngestResult:
        return self._ingest_one(file_path, force=force, on_progress=on_progress)

    def plan_folder(self, folder: Path) -> list[IngestPlan]:
        """Dry run: what ingest_folder would do, with zero LLM calls and zero
        writes. Exercises the vault walk, hashing, dedupe, and reject-log —
        and, via document_exists, OpenSearch connectivity."""
        return [self.plan_file(f, root_folder=folder) for f in discover_files(folder)]

    def plan_file(self, file_path: Path, root_folder: Path | None = None) -> IngestPlan:
        doc_id, _sha, _size = tp.file_identity(file_path)
        collection = derive_collection(file_path, root_folder)
        rejection = self._reject_log.is_rejected(doc_id)
        if rejection:
            return IngestPlan(
                file=file_path,
                doc_id=doc_id,
                collection=collection,
                action="skip_rejected",
                reason=rejection["last_error"],
            )
        if self._store.document_exists(doc_id):
            return IngestPlan(
                file=file_path, doc_id=doc_id, collection=collection, action="skip_exists"
            )
        return IngestPlan(
            file=file_path, doc_id=doc_id, collection=collection, action="ingest"
        )

    def _ingest_one(
        self,
        file_path: Path,
        force: bool,
        root_folder: Path | None = None,
        on_progress: ProgressFn | None = None,
    ) -> IngestResult:
        def progress(stage: str) -> None:
            if on_progress:
                on_progress(file_path, stage)

        doc_id, file_sha256, file_size = tp.file_identity(file_path)
        collection = derive_collection(file_path, root_folder)

        # Reject log: skip files that have previously failed (bypass with --force)
        if not force:
            rejection = self._reject_log.is_rejected(doc_id)
            if rejection:
                _log.info("reject-skip %s (%s prior attempts)", file_path.name, rejection["attempts"])
                return IngestResult(file=file_path, status="rejected", error=rejection["last_error"])

        # Resumability: skip if already ingested (keyed on content-hash doc_id)
        if not force and self._store.document_exists(doc_id):
            return IngestResult(file=file_path, status="skipped")

        t0 = time.monotonic()

        progress("parsing")
        try:
            raw = tp.process_file(file_path, self._config)
        except LLMRateLimitExhausted as e:
            # Not the file's fault — don't reject-log it (that would make the
            # next run silently skip it); the caller should stop the run.
            _log.error("rate limited on %s: %s", file_path.name, e)
            return IngestResult(
                file=file_path, status="error", error=f"rate limited: {e}", rate_limited=True
            )
        except Exception as e:
            _log.error("PageIndex error on %s: %s", file_path.name, e)
            self._reject_log.record_failure(doc_id, file_path.name, file_path, f"PageIndex error: {e}")
            return IngestResult(file=file_path, status="error", error=str(e))

        progress("flattening")
        try:
            doc_record, tree_nodes, page_content = tp.flatten_tree(
                raw, file_path, self._config,
                doc_id=doc_id, file_sha256=file_sha256, file_size=file_size,
                collection=collection,
                summary_model=self._config.tree_generation_model,
            )
        except Exception as e:
            _log.error("tree flatten error on %s: %s", file_path.name, e)
            self._reject_log.record_failure(doc_id, file_path.name, file_path, f"tree flatten error: {e}")
            return IngestResult(file=file_path, status="error", error=str(e))

        # Generate embeddings for discovery fields (skipped if no embedding model configured)
        if self._config.embeddings_enabled:
            progress("embedding")
            try:
                texts_to_embed = [doc_record.description, doc_record.root_summary]
                embeddings = emb.generate_embeddings_batch(
                    [t for t in texts_to_embed if t], self._config
                )
                idx = 0
                if doc_record.description:
                    doc_record.description_embedding = embeddings[idx]
                    idx += 1
                if doc_record.root_summary:
                    doc_record.root_summary_embedding = embeddings[idx]
                doc_record.embedding_model = self._config.embedding_model
            except Exception as e:
                _log.warning("embedding failed on %s (%s), continuing without vectors", file_path.name, e)

        progress("storing")
        try:
            self._store.upsert_document(doc_record)
            self._store.upsert_tree_nodes(doc_id, tree_nodes)
            self._store.upsert_page_content(doc_id, page_content)
        except Exception as e:
            _log.error("storage error on %s: %s", file_path.name, e)
            self._reject_log.record_failure(doc_id, file_path.name, file_path, f"storage error: {e}")
            return IngestResult(file=file_path, status="error", error=str(e))

        self._reject_log.clear(doc_id)
        return IngestResult(
            file=file_path,
            status="ok",
            node_count=len(tree_nodes),
            page_record_count=len(page_content),
            elapsed_seconds=time.monotonic() - t0,
        )
