"""Thin CLI adapter over the librarian library.

All pipeline logic lives in ingestion/ and query/ — this module only parses
arguments, wires config, and prints. Long LLM phases get a heartbeat line
(call count + elapsed) so slow-by-design commands never look hung.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import click

from .config import Config, load_config


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    if not verbose:
        # litellm and the vendored fork are chatty at INFO
        logging.getLogger("LiteLLM").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("librarian.pageindex").setLevel(logging.WARNING)
        logging.getLogger("opensearch").setLevel(logging.WARNING)


def _load(ctx: click.Context) -> Config:
    return load_config(ctx.obj.get("config_path"))


# ── liveness feedback ─────────────────────────────────────────────────────────

_TTY = sys.stderr.isatty()


def _echo(msg: str) -> None:
    """Print a line to stderr, clearing any active heartbeat line first."""
    if _TTY:
        sys.stderr.write("\r\033[K")
    click.echo(msg, err=True)


class _Heartbeat:
    """Background stderr ticker proving long LLM phases are alive.

    On a TTY the line rewrites in place every 5s; otherwise (logs, pipes) a
    full line prints every 15s.
    """

    def __init__(self, get_line: Callable[[], str]) -> None:
        self._get_line = get_line
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> "_Heartbeat":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        if _TTY:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()

    def _run(self) -> None:
        interval = 5 if _TTY else 15
        while not self._stop.wait(interval):
            line = self._get_line()
            if not line:
                continue
            if _TTY:
                sys.stderr.write(f"\r\033[K{line}")
            else:
                sys.stderr.write(line + "\n")
            sys.stderr.flush()


class _Activity:
    """Tracks the current stage label + per-stage call/time baselines."""

    def __init__(self) -> None:
        from . import llm_activity
        llm_activity.install()
        self._calls = llm_activity.calls
        self.label = ""
        self.suffix = ""
        self._t0 = time.monotonic()
        self._calls0 = 0

    def stage(self, label: str, suffix: str = "") -> None:
        self.label = label
        self.suffix = suffix
        self._t0 = time.monotonic()
        self._calls0 = self._calls()

    def line(self) -> str:
        if not self.label:
            return ""
        calls = self._calls() - self._calls0
        mins, secs = divmod(int(time.monotonic() - self._t0), 60)
        return f"  {self.label}…  {calls} LLM calls{self.suffix}  {mins}m{secs:02d}s"


def _pdf_page_count(file_path: Path) -> int | None:
    if file_path.suffix.lower() != ".pdf":
        return None
    try:
        import pymupdf
        doc = pymupdf.open(file_path)
        count = doc.page_count
        doc.close()
        return count
    except Exception:
        return None


# ── commands ──────────────────────────────────────────────────────────────────

@click.group()
@click.option("--config", "config_path", type=click.Path(), default=None,
              help="Path to config.yaml (default: ./config.yaml)")
@click.option("-v", "--verbose", is_flag=True, help="Debug logging")
@click.pass_context
def main(ctx: click.Context, config_path: str | None, verbose: bool) -> None:
    """Librarian — ingest books into OpenSearch and query them for citations."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path
    _setup_logging(verbose)


@main.command("init-indexes")
@click.option("--reset", is_flag=True, help="Delete and recreate all indexes")
@click.pass_context
def init_indexes(ctx: click.Context, reset: bool) -> None:
    """Create (or reset) the OpenSearch indexes and search pipeline."""
    config = _load(ctx)
    click.echo(f"OpenSearch: {config.opensearch_host}:{config.opensearch_port}", err=True)
    from .opensearch.client import create_client
    from .opensearch.index_manager import IndexManager

    manager = IndexManager(create_client(config), config)
    results = manager.reset_all() if reset else manager.create_all()
    for name, action in results:
        click.echo(f"  {action:8s} {name}")


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show index doc counts, sizes, and health."""
    config = _load(ctx)
    click.echo(f"OpenSearch: {config.opensearch_host}:{config.opensearch_port}", err=True)
    from .opensearch.client import create_client
    from .opensearch.index_manager import IndexManager

    manager = IndexManager(create_client(config), config)
    for name, info in manager.status().items():
        count = info["doc_count"] if info["doc_count"] is not None else "—"
        click.echo(f"  {name:25s} {str(count):>8s} docs  {info['size']:>10s}  {info['health']}")


@main.command()
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--force", is_flag=True, help="Re-ingest even if already indexed or rejected")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Report what would be ingested/skipped without any LLM call or index write",
)
@click.pass_context
def ingest(ctx: click.Context, path: Path, force: bool, dry_run: bool) -> None:
    """Ingest a file or folder of PDFs/EPUBs/Markdown.

    Tree generation makes many LLM calls per book (roughly pages/15 to
    pages/4, plus one summary per section) — the heartbeat shows progress.
    --dry-run walks, hashes, and checks the index/reject log, but costs
    nothing: no LLM calls, no writes.
    """
    config = _load(ctx)
    from .ingestion.pipeline import IngestionPipeline

    pipeline = IngestionPipeline(config)

    if dry_run:
        from opensearchpy.exceptions import ConnectionError as OSConnectionError

        try:
            if path.is_dir():
                plans = pipeline.plan_folder(path)
            else:
                plans = [pipeline.plan_file(path)]
        except OSConnectionError:
            raise click.ClickException(
                f"OpenSearch unreachable at "
                f"{config.opensearch_host}:{config.opensearch_port} — "
                "the dry run checks the index for already-ingested books."
            )
        would_ingest = sum(1 for p in plans if p.action == "ingest")
        exists = sum(1 for p in plans if p.action == "skip_exists")
        rejected = sum(1 for p in plans if p.action == "skip_rejected")
        for p in plans:
            where = f"  [{p.collection}]" if p.collection else ""
            if p.action == "ingest":
                click.echo(f"  → would ingest {p.file.name}{where}")
            elif p.action == "skip_exists":
                click.echo(f"  - exists {p.file.name}  ({p.doc_id}){where}")
            else:
                click.echo(f"  - reject-logged {p.file.name}: {p.reason}{where}")
        collections = sorted({p.collection for p in plans if p.collection})
        click.echo(
            f"Dry run: {would_ingest} would ingest, {exists} already indexed, "
            f"{rejected} reject-logged"
        )
        if collections:
            click.echo(f"Collections: {', '.join(collections)}")
        return
    activity = _Activity()

    def on_progress(file: Path, stage: str) -> None:
        if stage == "parsing":
            pages = _pdf_page_count(file)
            est = ""
            if pages:
                lo = max(10, pages // 15 + 15)
                hi = max(20, pages // 4 + 50)
                est = f" (~{lo}–{hi} expected)"
                _echo(f"{file.name}  [{pages}p]")
            else:
                _echo(f"{file.name}")
            activity.stage(f"{file.name}: {stage}", est)
        else:
            activity.stage(f"{file.name}: {stage}")
        _echo(f"  {stage}…")

    with _Heartbeat(activity.line):
        if path.is_dir():
            results = pipeline.ingest_folder(path, force=force, on_progress=on_progress)
        else:
            results = [pipeline.ingest_file(path, force=force, on_progress=on_progress)]

    ok = sum(1 for r in results if r.status == "ok")
    skipped = sum(1 for r in results if r.status in ("skipped", "rejected"))
    failed = [r for r in results if r.status == "error"]
    for r in results:
        if r.status == "ok":
            mins, secs = divmod(int(r.elapsed_seconds), 60)
            click.echo(f"  ✓ {r.file.name}  {r.node_count} nodes, {r.page_record_count} page records  ({mins}m{secs:02d}s)")
        elif r.status == "error":
            click.echo(f"  ✗ {r.file.name}  {r.error}")
        else:
            click.echo(f"  - {r.file.name}  ({r.status})")
    click.echo(f"Done: {ok} ingested, {skipped} skipped, {len(failed)} failed")
    if any(r.rate_limited for r in results):
        click.echo("Run stopped early: provider rate limit exhausted retries. Re-run later to continue.")
    if failed:
        sys.exit(1)


@main.command()
@click.option("--host", default=None, help="Bind address (default: api.host from config)")
@click.option("--port", default=None, type=int, help="Port (default: api.port from config)")
@click.pass_context
def serve(ctx: click.Context, host: str | None, port: int | None) -> None:
    """Run the librarian HTTP API (FastAPI + SSE via uvicorn)."""
    config = _load(ctx)
    try:
        import uvicorn
    except ImportError:
        raise click.ClickException(
            "uvicorn not installed. Run: pip install 'librarian[api]'"
        )
    from .api import create_app

    app = create_app(config)
    uvicorn.run(app, host=host or config.api_host, port=port or config.api_port)


@main.command()
@click.argument("question")
@click.option("--doc-id", default=None, help="Scope to a single document")
@click.option("--top-k", default=None, type=int, help="Phase 1 candidate count")
@click.option("--json", "as_json", is_flag=True, help="Print the full result as JSON")
@click.pass_context
def query(ctx: click.Context, question: str, doc_id: str | None, top_k: int | None, as_json: bool) -> None:
    """Run the citation funnel for a question."""
    config = _load(ctx)
    from .query.orchestrator import QueryOrchestrator

    activity = _Activity()

    def on_event(evt: dict) -> None:
        etype = evt.get("type")
        if etype == "phase_started":
            activity.stage(evt["msg"].rstrip("…"))
            _echo(f"── {evt['msg']}")
        elif etype == "docs_shortlisted":
            _echo(f"   shortlist: {', '.join(evt['doc_names'])}")
        elif etype == "tool_call":
            _echo(f"   {evt['fn']}({json.dumps(evt['args'], separators=(',', ':'))})")
        elif etype == "selections":
            _echo(f"   {len(evt['items'])} section(s) selected")
        elif etype == "citation_ready":
            c = evt["citation"]
            _echo(f"   ✓ notes ready: {c['doc_name']} — {c['title']}")

    orchestrator = QueryOrchestrator(config)
    with _Heartbeat(activity.line):
        result = orchestrator.run(question, top_k=top_k, doc_id=doc_id, on_event=on_event)

    if as_json:
        click.echo(json.dumps(result.to_dict(), indent=2))
        return

    if not result.citations:
        click.echo("No citations found.")
        return

    for i, c in enumerate(result.citations, 1):
        pages = f"pp. {c.start_page}–{c.end_page}" if c.start_page else ""
        click.echo(f"\n[{i}] {c.doc_name} — {c.title}  {pages}")
        click.echo(f"    {c.blurb}")
        for note in c.liner_notes:
            marker = "" if note.anchor.status == "resolved" else "  (unanchored)"
            page = f"p.{note.anchor.page_number} " if note.anchor.page_number else ""
            click.echo(f"      {page}“{note.quote}”{marker}")
            click.echo(f"        → {note.comment}")


if __name__ == "__main__":
    main()
