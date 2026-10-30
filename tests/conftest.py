"""Shared fixtures. Every behavioural test runs on InMemoryGraph and, when configured, on Neo4j AuraDB.

Neo4j runs only if NEO4J_URI is set (in .env or shell) AND NEO4J_TEST_WIPE is set to '1' or
'force' in the real shell environment (NEVER read from .env).
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from dotenv import dotenv_values

from agent_repo.config import Settings
from agent_repo.graph.store import NODE_LABELS, GraphError, GraphStore, InMemoryGraph, Node
from agent_repo.repository import AgentRepository
from agent_repo.seeds import seed_repository
from agent_repo.tools import ToolRegistry, builtin_registry

if TYPE_CHECKING:
    from agent_repo.graph.neo4j_store import Neo4jGraph

MAINTAINER = "alice"
_OWNED_LABELS = NODE_LABELS | {"GraphNode"}
_PRINTED_WIPE_HOSTS: set[str] = set()
# Tests must never depend on the developer's real salt; use a fixed test-only value.
_TEST_SALT = "test-salt-0123456789abcdef0123456789abcdef"


def _env() -> dict[str, str]:
    """Load NEO4J_URI/USER/PASSWORD from .env, overlaid with os.environ.
    NEO4J_TEST_WIPE is stripped from .env so it is strictly a shell-only variable.
    A missing or placeholder (``change-me…``) AGENT_REPO_HASH_SALT is replaced with a
    fixed test salt so tests do not depend on the developer's .env.
    """
    file_values = {
        k: v for k, v in dotenv_values(Path(".env")).items()
        if v is not None and k != "NEO4J_TEST_WIPE"
    } if Path(".env").is_file() else {}
    env = {**file_values, **os.environ}
    if "NEO4J_TEST_WIPE" not in os.environ:
        env.pop("NEO4J_TEST_WIPE", None)
    salt = env.get("AGENT_REPO_HASH_SALT", "")
    if not salt or salt.strip().lower().startswith("change-me"):
        env["AGENT_REPO_HASH_SALT"] = _TEST_SALT
    return env


@pytest.fixture(scope="session", autouse=True)
def _default_test_salt() -> Iterator[None]:
    """Expose a test salt in os.environ when none is set, so code paths that call
    ``Settings.from_env()`` (e.g. ``cli.main``) never fail on the .env placeholder.
    Tests that need a specific salt still override it themselves.
    """
    current = os.environ.get("AGENT_REPO_HASH_SALT", "")
    if current and not current.strip().lower().startswith("change-me"):
        yield
        return
    os.environ["AGENT_REPO_HASH_SALT"] = _TEST_SALT
    try:
        yield
    finally:
        if current:
            os.environ["AGENT_REPO_HASH_SALT"] = current
        else:
            os.environ.pop("AGENT_REPO_HASH_SALT", None)


def wipe_neo4j_for_tests(store: Neo4jGraph, shell_env: Mapping[str, str]) -> None:
    """Safely wipe a Neo4j / AuraDB test database. Exists ONLY in test code.

    - Requires NEO4J_TEST_WIPE='1' or 'force' in `shell_env` (never from .env).
    - Prints the target host before wiping.
    - Refuses to wipe if the database contains nodes with labels outside this project's
      schema, unless NEO4J_TEST_WIPE='force'.
    """
    mode = shell_env.get("NEO4J_TEST_WIPE", "")
    if mode not in ("1", "force"):
        raise GraphError(
            "Refusing to wipe Neo4j: set NEO4J_TEST_WIPE=1 in your shell environment on a throwaway database."
        )
    first_wipe = store.host not in _PRINTED_WIPE_HOSTS
    if first_wipe:
        print(f"\n[NEO4J_TEST_WIPE] Wiping Neo4j database {store.database!r} on host {store.host!r} (mode={mode})")

    def _wipe_tx(tx: object) -> None:
        if first_wipe and mode != "force":
            rows = [r.data() for r in tx.run("MATCH (n) UNWIND labels(n) AS lbl RETURN DISTINCT lbl")]  # type: ignore[attr-defined]
            foreign = sorted({r["lbl"] for r in rows if r["lbl"] not in _OWNED_LABELS})
            if foreign:
                raise GraphError(
                    f"Refusing to wipe Neo4j host {store.host!r}: database contains foreign node labels "
                    f"{foreign} not owned by agent_repo. Set NEO4J_TEST_WIPE=force in your shell to override."
                )
        tx.run("MATCH (n) DETACH DELETE n")  # type: ignore[attr-defined]

    store._exec_managed(write=True, fn=_wipe_tx)  # noqa: SLF001
    _PRINTED_WIPE_HOSTS.add(store.host)


@pytest.fixture(scope="session")
def _neo4j_session_store() -> Iterator[Neo4jGraph | None]:
    env = _env()
    settings = Settings.from_env(env)
    wipe_mode = os.environ.get("NEO4J_TEST_WIPE", "")
    if settings.neo4j is None or wipe_mode not in ("1", "force"):
        yield None
        return
    from agent_repo.graph.neo4j_store import Neo4jGraph

    store = Neo4jGraph(settings.neo4j)
    yield store
    store.close()


@pytest.fixture(params=[
    pytest.param("memory", id="memory"),
    pytest.param("neo4j", id="neo4j", marks=pytest.mark.neo4j),
])
def graph(request: pytest.FixtureRequest, _neo4j_session_store: Neo4jGraph | None) -> Iterator[GraphStore]:
    if request.param == "memory":
        yield InMemoryGraph()
        return
    env = _env()
    settings = Settings.from_env(env)
    if settings.neo4j is None:
        pytest.skip("Neo4j backend: NEO4J_URI not set in .env or environment")
    if os.environ.get("NEO4J_TEST_WIPE", "") not in ("1", "force"):
        pytest.skip("Neo4j backend: set NEO4J_TEST_WIPE=1 in the shell environment to run against AuraDB")
    assert _neo4j_session_store is not None
    wipe_neo4j_for_tests(_neo4j_session_store, os.environ)
    yield _neo4j_session_store


@pytest.fixture
def registry() -> ToolRegistry:
    return builtin_registry()


@pytest.fixture
def repo(graph: GraphStore, registry: ToolRegistry) -> AgentRepository:
    return AgentRepository(graph, registry)


@pytest.fixture
def seeded(repo: AgentRepository) -> AgentRepository:
    seed_repository(repo, MAINTAINER)
    return repo


def tamper(graph: GraphStore, node: Node, key: str, value: str) -> None:
    """Bypass the store's immutability guard, as an attacker with DB access could."""
    if isinstance(graph, InMemoryGraph):
        graph._nodes[node.id] = Node(node.id, node.label, {**node.props, key: value})  # noqa: SLF001
    else:
        graph._write(f"MATCH (n:GraphNode {{id: $id}}) SET n.{key} = $v", id=node.id, v=value)  # type: ignore[attr-defined]  # noqa: SLF001
