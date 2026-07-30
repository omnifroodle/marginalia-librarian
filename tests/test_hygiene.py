"""Library hygiene: no env mutation, no print() in library code.

These encode the porting rules from the plan — the old repo mutated os.environ
in constructors and printed from core modules, which blocked embedding it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src" / "librarian"
CB = SRC / "cb"
PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def _source_files():
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def _cb_files():
    return [p for p in CB.rglob("*.py") if "__pycache__" not in p.parts]


def test_no_os_environ_writes():
    # Reads (os.environ.get / os.getenv) are fine; subscript access or
    # setdefault imply mutation.
    pattern = re.compile(r"os\.environ\[|os\.environ\.setdefault")
    offenders = [
        str(p.relative_to(SRC))
        for p in _source_files()
        if pattern.search(p.read_text())
    ]
    assert offenders == [], f"os.environ mutation in: {offenders}"


def test_no_undefined_names():
    # Guards the star-import removal in the pageindex port: the fork's modules
    # used to receive names like `asyncio`/`BytesIO` implicitly via
    # `from .utils import *`; a missed one only explodes at runtime, mid-ingest.
    import io

    from pyflakes.api import checkPath
    from pyflakes.reporter import Reporter

    out, err = io.StringIO(), io.StringIO()
    reporter = Reporter(out, err)
    for p in _source_files():
        checkPath(str(p), reporter)
    undefined = [l for l in out.getvalue().splitlines() if "undefined name" in l]
    assert undefined == [], f"undefined names: {undefined}"


# ── stricter rules, scoped to the new Couchbase code ─────────────────────────
# The legacy tree (and the vendored pageindex fork) can't pass these and isn't
# worth churning; cb/ is new code and is held to the migration's own bar.
# CLAUDE.md: "enforced by tests/test_hygiene.py — binding on cb/ too".


def test_cb_has_no_unused_imports():
    import io

    from pyflakes.api import checkPath
    from pyflakes.reporter import Reporter

    out, err = io.StringIO(), io.StringIO()
    reporter = Reporter(out, err)
    for p in _cb_files():
        checkPath(str(p), reporter)
    unused = [l for l in out.getvalue().splitlines() if "imported but unused" in l]
    assert unused == [], f"unused imports in cb/: {unused}"


def test_cb_has_no_blanket_except():
    """No `except Exception:` / bare `except:` in cb/.

    A blanket catch on a backend call turns "your password is wrong",
    "that bucket doesn't exist" and "the cluster is unreachable" into one
    indistinguishable failure — and those are exactly the three the SDK
    bothers to tell apart. Catch the specific exception you can act on.
    """
    offenders = []
    for p in _cb_files():
        tree = ast.parse(p.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            caught = node.type
            if caught is None:
                offenders.append(f"{p.relative_to(SRC)}:{node.lineno} bare except:")
            elif isinstance(caught, ast.Name) and caught.id in {"Exception", "BaseException"}:
                offenders.append(f"{p.relative_to(SRC)}:{node.lineno} except {caught.id}:")
    assert offenders == [], f"blanket except in cb/: {offenders}"


@pytest.mark.lesson(1)
def test_couchbase_sdk_is_a_declared_dependency():
    """The SDK belongs in pyproject.toml with a version constraint.

    `pip install -e ".[api,dev]"` — the documented setup command, and the one
    training-tools/dev-setup.sh runs — reads only pyproject. A dependency that
    exists solely in the local venv (or in a parallel requirements.txt) makes a
    fresh clone fail at import time.
    """
    text = PYPROJECT.read_text()
    deps_match = re.search(r"^dependencies\s*=\s*\[(.*?)^\]", text, re.S | re.M)
    assert deps_match, "could not find [project] dependencies in pyproject.toml"
    deps = deps_match.group(1)

    entry = re.search(r'"(couchbase[^"]*)"', deps)
    assert entry, (
        "`couchbase` is not in pyproject.toml [project] dependencies — a fresh "
        "clone following CLAUDE.md would not install the SDK."
    )
    assert re.search(r"[<>=~!]", entry.group(1)), (
        f'"{entry.group(1)}" has no version constraint. Every other dependency '
        "here carries a floor; pick one and be able to say why (Lesson 9's "
        "vector search needs the 4.x line)."
    )


def test_no_print_in_library_code():
    # cli.py is the presentation layer (uses click.echo anyway).
    pattern = re.compile(r"(?<![\w.])print\(")
    offenders = []
    for p in _source_files():
        if p.name == "cli.py":
            continue
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if pattern.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{p.relative_to(SRC)}:{i}")
    assert offenders == [], f"print() in library code: {offenders}"
