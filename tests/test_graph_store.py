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


def test_neo4j_uri_validation_and_error_translation() -> None:
    from agent_repo.graph.neo4j_store import Neo4jConnectionError, validate_neo4j_uri, _translate_driver_error
    from neo4j.exceptions import AuthError, ServiceUnavailable
    import socket

    with pytest.raises(Neo4jConnectionError, match="missing or empty"):
        validate_neo4j_uri("")

    with pytest.raises(Neo4jConnectionError, match="placeholder"):
        validate_neo4j_uri("neo4j+s://xxxx.databases.neo4j.io")

    with pytest.raises(Neo4jConnectionError, match="placeholder"):
        validate_neo4j_uri("neo4j+s://<db_id>.databases.neo4j.io")

    with pytest.raises(Neo4jConnectionError, match="not a valid URI"):
        validate_neo4j_uri("invalid_uri_without_scheme")

    host = validate_neo4j_uri("neo4j+s://testdb123.databases.neo4j.io")
    assert host == "testdb123.databases.neo4j.io"

    # Test error translation
    auth_err = _translate_driver_error(AuthError("unauthorized access"), host)
    assert isinstance(auth_err, Neo4jConnectionError)
    assert "authentication failed" in str(auth_err).lower()

    pause_err = _translate_driver_error(RuntimeError("instance is not running (paused)"), host)
    assert "paused" in str(pause_err).lower()

    dns_err = _translate_driver_error(socket.gaierror(-2, "getaddrinfo failed"), host)
    assert "dns lookup failed" in str(dns_err).lower()

    timeout_err = _translate_driver_error(ServiceUnavailable("connection timed out"), host)
    assert "could not connect" in str(timeout_err).lower()


def test_neo4j_graph_crud_and_immutability_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Offline non-destructive test of Neo4jGraph driver interaction without touching any live database."""
    from agent_repo.graph.neo4j_store import Neo4jGraph, GraphError
    from agent_repo.config import Neo4jSettings
    from neo4j import GraphDatabase

    queries_run: list[tuple[str, dict]] = []

    class FakeRecord:
        def __init__(self, d: dict):
            self._d = d
        def data(self):
            return self._d

    class FakeResult:
        def __init__(self, items: list[dict]):
            self._items = [FakeRecord(it) for it in items]
        def __iter__(self):
            return iter(self._items)

    class FakeTx:
        def run(self, cypher: str, params: dict | None = None):
            queries_run.append((cypher, params or {}))
            if "CREATE CONSTRAINT" in cypher:
                return FakeResult([])
            if "CREATE (n:" in cypher or "MATCH (n:GraphNode" in cypher:
                p = params.get("props", {}) if params else {}
                nid = params.get("id", "node-123") if params else "node-123"
                return FakeResult([{"id": nid, "label": "AgentVersion", "props": {"id": nid, "manifest_json": "{}", "status": "candidate", **p}}])
            if "MATCH (a:GraphNode" in cypher or "CREATE (a)-[r:" in cypher:
                rid = params.get("id", "rel-123") if params else "rel-123"
                return FakeResult([{"id": rid, "type": "USES_TOOL", "start": "a-1", "end": "b-1", "props": {"id": rid, "order": 0}}])
            return FakeResult([])

    class FakeSession:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute_write(self, fn):
            return fn(FakeTx())
        def execute_read(self, fn):
            return fn(FakeTx())

    class FakeDriver:
        def session(self, database="neo4j"):
            return FakeSession()
        def close(self):
            pass

    monkeypatch.setattr(GraphDatabase, "driver", lambda *args, **kwargs: FakeDriver())

    settings = Neo4jSettings(
        uri="neo4j+s://mock-db.databases.neo4j.io",
        user="neo4j",
        password="mock-password",
        database="neo4j",
    )
    g = Neo4jGraph(settings)

    # Initial schema constraint was executed
    assert any("CREATE CONSTRAINT graphnode_id" in q[0] for q in queries_run)

    # create_node
    n = g.create_node("Agent", {"name": "test_agent"})
    assert len(n.id) == 32
    assert n.label == "AgentVersion"

    # immutable property guard triggers before writing
    with pytest.raises(GraphError, match="immutable"):
        g.set_props("node-123", {"manifest_json": '{"tampered": true}'})

    # batch tool sync
    g.sync_tools_batch([{"name": "test_tool", "risk": "low"}])
    assert any("UNWIND $rows AS row" in q[0] for q in queries_run)

    g.close()


def test_wipe_neo4j_for_tests_safety_guards() -> None:
    """Verify that wipe_neo4j_for_tests strictly protects against wiping non-test databases."""
    from tests.conftest import wipe_neo4j_for_tests
    from agent_repo.config import Neo4jSettings
    from agent_repo.graph.neo4j_store import Neo4jGraph
    from neo4j import GraphDatabase

    class FakeRecord:
        def __init__(self, d: dict):
            self._d = d
        def data(self):
            return self._d

    class FakeResult:
        def __init__(self, items: list[dict]):
            self._items = [FakeRecord(it) for it in items]
        def __iter__(self):
            return iter(self._items)

    class FakeTx:
        def run(self, cypher: str, params: dict | None = None):
            if "count(n)" in cypher:
                return FakeResult([{"count": 100}])  # non-empty database
            return FakeResult([])

    class FakeSession:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute_read(self, fn):
            return fn(FakeTx())
        def execute_write(self, fn):
            return fn(FakeTx())

    class FakeDriver:
        def session(self, database="neo4j"):
            return FakeSession()
        def close(self):
            pass

    import pytest
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(GraphDatabase, "driver", lambda *args, **kwargs: FakeDriver())
    g = Neo4jGraph(Neo4jSettings(uri="neo4j+s://mock-db.databases.neo4j.io", user="neo4j", password="p", database="neo4j"))

    from agent_repo.graph.store import GraphError

    # Fails if NEO4J_TEST_WIPE is not set to 1
    with pytest.raises(GraphError, match="NEO4J_TEST_WIPE"):
        wipe_neo4j_for_tests(g, {})

    monkeypatch.undo()


