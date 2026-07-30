"""Library hygiene: no env mutation, no print() in library code.

These encode the porting rules from the plan — the old repo mutated os.environ
in constructors and printed from core modules, which blocked embedding it.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "librarian"


def _source_files():
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


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
