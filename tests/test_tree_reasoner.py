"""Single-doc scoping in agentic Phase 3.

The reader's select-text-and-ask path calls the funnel with doc_id set. The
model must not be able to reach a second document from there — not via the
search_corpus tool, not by passing someone else's doc_id to a pinned tool, and
not by submitting a selection out of a document it was never given.
"""

from __future__ import annotations

import json

import pytest

from librarian.query import tree_reasoner as tr
from librarian.query.tree_reasoner import TreeReasoner

from .conftest import MockConfig


# ── fakes ────────────────────────────────────────────────────────────────────

class FakeFunction:
    def __init__(self, name: str, args: dict) -> None:
        self.name = name
        self.arguments = json.dumps(args)


class FakeToolCall:
    def __init__(self, name: str, args: dict, id_: str = "call_1") -> None:
        self.id = id_
        self.function = FakeFunction(name, args)


class FakeMessage:
    def __init__(self, tool_calls: list[FakeToolCall]) -> None:
        self.tool_calls = tool_calls

    def model_dump(self, exclude_none: bool = False) -> dict:
        return {"role": "assistant", "tool_calls": []}


class FakeResponse:
    def __init__(self, tool_calls: list[FakeToolCall]) -> None:
        self.choices = [type("Choice", (), {"message": FakeMessage(tool_calls)})()]


class FakeLLM:
    """Replays a script of tool calls and records the tools it was offered."""

    def __init__(self, script: list[list[FakeToolCall]]) -> None:
        self._script = list(script)
        self.tools_seen: list[dict] = []
        self.calls = 0

    def __call__(self, **kwargs):
        self.calls += 1
        self.tools_seen = kwargs.get("tools", [])
        if not self._script:
            raise AssertionError("FakeLLM ran out of scripted turns")
        return FakeResponse(self._script.pop(0))


class FakeSearcher:
    """Records what the teleport tools asked for."""

    def __init__(self) -> None:
        self.search_page_calls: list[dict] = []
        self.hybrid_calls: list[str] = []
        self.trees_fetched: list[str] = []

    def search_page_content(self, query, top_k=10, doc_id=None):
        self.search_page_calls.append({"query": query, "doc_id": doc_id})
        return [{"doc_id": doc_id or "any", "node_id": "0001", "snippet": "…"}]

    def hybrid_search(self, query, query_embedding=None, top_k=10):
        self.hybrid_calls.append(query)
        return [{"doc_id": "other_doc", "doc_name": "Other Book", "description": "…"}]

    def get_tree_nodes(self, doc_id):
        self.trees_fetched.append(doc_id)
        return [{"doc_id": doc_id, "node_id": "0001", "title": "Elsewhere", "depth": 0}]

    def reconstruct_tree(self, flat):
        return [{"node_id": "0001", "title": "Elsewhere", "nodes": []}]


DOC_TREES = {"doc1": [{"node_id": "0001", "title": "Stunts", "summary": "…", "nodes": []}]}


def _tool_names(tools: list[dict]) -> set[str]:
    return {t["function"]["name"] for t in tools}


def _reasoner(monkeypatch, script) -> tuple[TreeReasoner, FakeLLM, FakeSearcher]:
    llm = FakeLLM(script)
    monkeypatch.setattr(tr.litellm, "completion", llm)
    searcher = FakeSearcher()
    return TreeReasoner(MockConfig(), searcher=searcher), llm, searcher


SUBMIT_DOC1 = [FakeToolCall("submit_selections", {
    "selections": [{"doc_id": "doc1", "node_id": "0001", "rationale": "the stunt rules"}]
})]


# ── tests ────────────────────────────────────────────────────────────────────

def test_single_doc_withholds_search_corpus(monkeypatch):
    reasoner, llm, _ = _reasoner(monkeypatch, [SUBMIT_DOC1])

    reasoner.select_nodes("power stunts", dict(DOC_TREES), single_doc=True)

    names = _tool_names(llm.tools_seen)
    assert "search_corpus" not in names
    assert {"view_tree", "expand_node", "submit_selections", "search_pages"} <= names


def test_corpus_wide_run_still_offers_search_corpus(monkeypatch):
    reasoner, llm, _ = _reasoner(monkeypatch, [SUBMIT_DOC1])

    reasoner.select_nodes("power stunts", dict(DOC_TREES))

    assert "search_corpus" in _tool_names(llm.tools_seen)


def test_search_pages_is_pinned_to_the_scoped_doc(monkeypatch):
    """The model asks for another document's pages; it gets this one's."""
    script = [
        [FakeToolCall("search_pages", {"query": "stunts", "doc_id": "some_other_doc"})],
        SUBMIT_DOC1,
    ]
    reasoner, _, searcher = _reasoner(monkeypatch, script)

    reasoner.select_nodes("power stunts", dict(DOC_TREES), single_doc=True)

    assert searcher.search_page_calls == [{"query": "stunts", "doc_id": "doc1"}]


def test_search_corpus_is_refused_even_if_the_model_calls_it(monkeypatch):
    """The tool isn't offered, but the handler must still refuse it — a
    hallucinated call would otherwise pull another document's tree into play."""
    script = [
        [FakeToolCall("search_corpus", {"query": "stunts"})],
        SUBMIT_DOC1,
    ]
    doc_trees = dict(DOC_TREES)
    reasoner, _, searcher = _reasoner(monkeypatch, script)

    reasoner.select_nodes("power stunts", doc_trees, single_doc=True)

    assert searcher.hybrid_calls == []
    assert list(doc_trees) == ["doc1"]


def test_walk_up_is_pinned_to_the_scoped_doc(monkeypatch):
    """walk_up_from_node loads an unknown doc's tree on demand — pin it, or it
    becomes a back door to a second document."""
    script = [
        [FakeToolCall("walk_up_from_node", {"doc_id": "some_other_doc", "node_id": "0001"})],
        SUBMIT_DOC1,
    ]
    doc_trees = dict(DOC_TREES)
    reasoner, _, searcher = _reasoner(monkeypatch, script)

    reasoner.select_nodes("power stunts", doc_trees, single_doc=True)

    assert searcher.trees_fetched == []
    assert list(doc_trees) == ["doc1"]


def test_foreign_selections_are_dropped(monkeypatch):
    script = [[FakeToolCall("submit_selections", {"selections": [
        {"doc_id": "doc1", "node_id": "0001", "rationale": "the stunt rules"},
        {"doc_id": "other_doc", "node_id": "0009", "rationale": "smuggled in"},
    ]})]]
    reasoner, _, _ = _reasoner(monkeypatch, script)

    selections = reasoner.select_nodes("power stunts", dict(DOC_TREES), single_doc=True)

    assert [(s.doc_id, s.node_id) for s in selections] == [("doc1", "0001")]


def test_corpus_wide_run_keeps_multi_doc_selections(monkeypatch):
    script = [[FakeToolCall("submit_selections", {"selections": [
        {"doc_id": "doc1", "node_id": "0001", "rationale": "a"},
        {"doc_id": "other_doc", "node_id": "0009", "rationale": "b"},
    ]})]]
    reasoner, _, _ = _reasoner(monkeypatch, script)

    selections = reasoner.select_nodes("power stunts", dict(DOC_TREES))

    assert len(selections) == 2


@pytest.mark.parametrize("single_doc", [True, False])
def test_selections_carry_rationale(monkeypatch, single_doc):
    reasoner, _, _ = _reasoner(monkeypatch, [SUBMIT_DOC1])

    selections = reasoner.select_nodes(
        "power stunts", dict(DOC_TREES), single_doc=single_doc
    )

    assert selections[0].rationale == "the stunt rules"
