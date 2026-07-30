"""Repo-root pytest plumbing: the curriculum `lesson` marker.

Tests are tagged with the lesson that introduces them (CURRICULUM.md), so a
run can be scoped to "everything that should be green by now":

    pytest -m integration --lesson 1     # lessons 1 and earlier only
    pytest --lesson 3                    # unit suite through lesson 3
    pytest -m integration                # everything that exists

Untagged tests (the whole pre-migration suite) always run — they are the
standing regression baseline, not part of any lesson.

This lives at the repo root because `pytest_addoption` is only honoured in an
initial conftest.
"""

from __future__ import annotations


def pytest_addoption(parser):
    parser.addoption(
        "--lesson",
        action="store",
        type=int,
        default=None,
        metavar="N",
        help="Run only tests through curriculum lesson N (CURRICULUM.md). "
        "Tests without a lesson marker always run.",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "lesson(n): curriculum lesson that introduces this test (CURRICULUM.md)",
    )


def _lesson_of(item) -> int | None:
    marker = item.get_closest_marker("lesson")
    return marker.args[0] if marker and marker.args else None


def pytest_collection_modifyitems(config, items):
    limit = config.getoption("--lesson")
    if limit is None:
        return
    selected, deselected = [], []
    for item in items:
        lesson = _lesson_of(item)
        (deselected if lesson is not None and lesson > limit else selected).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected
