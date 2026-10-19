from __future__ import annotations

from pathlib import Path

import pytest

from agent_repo.graph.store import GraphError, GraphStore, InMemoryGraph


def test_schema_is_enforced(graph: GraphStore) -> None:
    with pytest.raises(GraphError):
        graph.create_node("Banana", {})
    a = graph.create_node("Agent", {"name": "a"})
    with pytest.raises(GraphError):
        graph.create_rel("LIKES", a.id, a.id)
    with pytest.raises(GraphError):
        graph.create_node("Agent", {"nested": {"x": 1}})
    with pytest.raises(GraphError):
        graph.create_node("Agent", {"bad key": 1})


@pytest.mark.neo4j_smoke
def test_crud_and_rel_queries(graph: GraphStore) -> None:
    a = graph.create_node("Agent", {"name": "a"})
    v = graph.create_node("Tool", {"name": "t", "risk": "low"})
    r = graph.create_rel("USES_TOOL", a.id, v.id, {"order": 0})
    assert graph.get_node(a.id) == a
    assert [n.id for n in graph.find_nodes("Tool", {"risk": "low"})] == [v.id]
    assert graph.find_nodes("Tool", {"risk": "high"}) == []
    assert [x.id for x in graph.rels(start=a.id, rel_type="USES_TOOL")] == [r.id]
    assert graph.set_rel_props(r.id, {"active": False}).props == {"order": 0, "active": False}
    assert graph.set_props(a.id, {"x": 1}).props == {"name": "a", "x": 1}


@pytest.mark.neo4j_smoke
def test_store_has_no_delete_operation(graph: GraphStore) -> None:
    assert not [m for m in dir(graph) if "delete" in m.lower() or m.lower().startswith("remove")]


@pytest.mark.neo4j_smoke
def test_immutable_props_refused_by_store(graph: GraphStore) -> None:
    n = graph.create_node("AgentVersion", {"manifest_json": "{}", "content_hash": "h", "status": "candidate"})
    for key in ("manifest_json", "content_hash"):
        with pytest.raises(GraphError, match="immutable"):
            graph.set_props(n.id, {key: "x"})
    assert graph.set_props(n.id, {"status": "pending_approval"}).props["status"] == "pending_approval"


def test_json_roundtrip(tmp_path: Path) -> None:
    g = InMemoryGraph()
    a = g.create_node("Agent", {"name": "a", "tags": ["x", "y"]})
    t = g.create_node("Tool", {"name": "t"})
    g.create_rel("USES_TOOL", a.id, t.id, {"order": 0})
    path = tmp_path / "g.json"
    g.save(path)
    loaded = InMemoryGraph.load(path)
    assert loaded.all_nodes() == g.all_nodes()
    assert loaded.all_rels() == g.all_rels()

