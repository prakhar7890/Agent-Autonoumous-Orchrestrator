"""Neo4j 5 / AuraDB Free backend for GraphStore (official `neo4j` driver).

Every node carries its domain label plus the helper label `GraphNode` (unique `id`
constraint) so lookups by id use an index. Labels and relationship types are checked
against the schema whitelist before being placed into Cypher; all values are parameters.

Optimised for cloud latency and AuraDB Free connection stability:
- Uses managed transactions (`session.execute_read` / `session.execute_write`) with
  `max_transaction_retry_time=60.0`, `max_connection_lifetime=200.0`, and
  `keep_alive=True`, plus an outer retry loop on `SessionExpired` / `ServiceUnavailable`
  so dropped idle cloud connections never abort a test or request.
- `set_props` performs a single atomic round-trip (fetching the node only if the update
  attempts to touch an immutable key).
- `sync_tools_batch` and `seed_agents_batch` upsert all tools, seed agents, versions, and
  relationships in single `UNWIND` managed transactions (1 round-trip instead of ~85).
- Fails early with actionable messages for placeholder URIs, DNS failures, auth errors,
  blocked port 7687 timeouts, or paused Aura instances.

Note: there is NO wipe or delete method on `Neo4jGraph`; test wiping lives exclusively in
`tests/conftest.py`.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from urllib.parse import urlparse

from neo4j import Driver, GraphDatabase, ManagedTransaction
from neo4j.exceptions import (
    AuthError, ConfigurationError, Neo4jError, ServiceUnavailable, SessionExpired, TransientError,
)

from agent_repo.config import Neo4jSettings
from agent_repo.graph.store import (
    IMMUTABLE_PROPS, NODE_LABELS, GraphError, Node, Props, Rel, Scalar, check_label, check_mutation,
    check_props, check_rel_type, new_id,
)

_HELPER = "GraphNode"
_ALL_IMMUTABLE_KEYS = frozenset().union(*IMMUTABLE_PROPS.values())
_NODE_RET = f"n.id AS id, [l IN labels(n) WHERE l <> '{_HELPER}'][0] AS label, properties(n) AS props"
_REL_RET = "r.id AS id, type(r) AS type, a.id AS start, b.id AS end, properties(r) AS props"


class Neo4jConnectionError(GraphError):
    """Actionable connection/configuration error for Neo4j / AuraDB."""


def validate_neo4j_uri(uri: str) -> str:
    cleaned = (uri or "").strip()
    if not cleaned:
        raise Neo4jConnectionError("NEO4J_URI is missing or empty. Set it in .env (e.g. neo4j+s://xxxx.databases.neo4j.io).")
    if "<" in cleaned or ">" in cleaned or "xxxx" in cleaned.lower():
        raise Neo4jConnectionError(
            f"NEO4J_URI ({cleaned!r}) still contains placeholder characters ('<', '>', or 'xxxx'). "
            "Replace it with your real AuraDB URI in .env."
        )
    parsed = urlparse(cleaned)
    if not parsed.scheme or not parsed.hostname:
        raise Neo4jConnectionError(f"NEO4J_URI ({cleaned!r}) is not a valid URI (expected neo4j+s://<host>).")
    return parsed.hostname


def _translate_driver_error(exc: Exception, host: str) -> Neo4jConnectionError:
    msg = str(exc)
    low = msg.lower()
    if isinstance(exc, AuthError) or "unauthorized" in low or "authentication" in low:
        return Neo4jConnectionError(
            f"Neo4j authentication failed for host {host!r}. Check NEO4J_USER and NEO4J_PASSWORD in .env."
        )
    if "pause" in low or "resume" in low or "instance is not running" in low:
        return Neo4jConnectionError(
            f"AuraDB instance at {host!r} appears to be paused or not running — resume it in the Neo4j Aura console."
        )
    if isinstance(exc, socket.gaierror) or "getaddrinfo failed" in low or "name or service not known" in low or "nodename nor servname" in low:
        return Neo4jConnectionError(
            f"DNS lookup failed for Neo4j host {host!r}. Check NEO4J_URI in .env or whether the Aura instance is paused (resume it in the Aura console)."
        )
    if isinstance(exc, TimeoutError) or "timed out" in low or "timeout" in low or "unable to retrieve routing" in low or isinstance(exc, ServiceUnavailable):
        return Neo4jConnectionError(
            f"Could not connect to Neo4j at {host!r}: {msg}. "
            "Your network or firewall may block Bolt port 7687, or the AuraDB Free instance may be paused (resume it in the Aura console)."
        )
    return Neo4jConnectionError(f"Neo4j error on {host!r}: {msg}")


def _node(rec: Mapping[str, Any]) -> Node:
    props = dict(rec["props"])
    props.pop("id", None)
    return Node(rec["id"], rec["label"], props)


def _rel(rec: Mapping[str, Any]) -> Rel:
    props = dict(rec["props"])
    props.pop("id", None)
    return Rel(rec["id"], rec["type"], rec["start"], rec["end"], props)


class Neo4jGraph:
    def __init__(self, settings: Neo4jSettings, id_factory: Callable[[], str] = new_id) -> None:
        self._host = validate_neo4j_uri(settings.uri)
        self._db = settings.database or "neo4j"
        self._new_id = id_factory
        try:
            self._driver: Driver = GraphDatabase.driver(
                settings.uri,
                auth=(settings.user, settings.password),
                connection_timeout=30.0,
                max_transaction_retry_time=60.0,
                max_connection_lifetime=200.0,
                liveness_check_timeout=5.0,
                keep_alive=True,
            )
            self._ensure_schema()
        except Neo4jConnectionError:
            raise
        except (Neo4jError, ServiceUnavailable, SessionExpired, AuthError, ConfigurationError, OSError) as exc:
            raise _translate_driver_error(exc, self._host) from exc

    @property
    def host(self) -> str:
        return self._host

    @property
    def database(self) -> str:
        return self._db

    def close(self) -> None:
        self._driver.close()

    def _exec_managed(self, write: bool, fn: Callable[[ManagedTransaction], Any]) -> Any:
        """Execute `fn` in a managed read or write transaction with outer retry on SessionExpired."""
        last_exc: Exception | None = None
        for attempt in range(4):
            try:
                with self._driver.session(database=self._db) as session:
                    if write:
                        return session.execute_write(fn)
                    return session.execute_read(fn)
            except (SessionExpired, ServiceUnavailable, TransientError, OSError) as exc:
                last_exc = exc
                if attempt < 3:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise _translate_driver_error(exc, self._host) from exc
            except (Neo4jError, AuthError) as exc:
                raise _translate_driver_error(exc, self._host) from exc
        assert last_exc is not None
        raise _translate_driver_error(last_exc, self._host)

    def _read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        def _tx(tx: ManagedTransaction) -> list[dict[str, Any]]:
            return [r.data() for r in tx.run(cypher, params)]

        return self._exec_managed(write=False, fn=_tx)

    def _write(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        def _tx(tx: ManagedTransaction) -> list[dict[str, Any]]:
            return [r.data() for r in tx.run(cypher, params)]

        return self._exec_managed(write=True, fn=_tx)

    def _ensure_schema(self) -> None:
        self._write(f"CREATE CONSTRAINT graphnode_id IF NOT EXISTS FOR (n:{_HELPER}) REQUIRE n.id IS UNIQUE")

    def sync_tools_batch(self, tools_props: Iterable[Props]) -> None:
        """Upsert all Tool nodes in a single network round-trip."""
        rows: list[dict[str, Any]] = []
        for p in tools_props:
            check_props(p)
            rows.append({"props": dict(p), "id": self._new_id()})
        if not rows:
            return
        self._write(
            f"UNWIND $rows AS row "
            f"MERGE (n:Tool:{_HELPER} {{name: row.props.name}}) "
            f"ON CREATE SET n.id = row.id "
            f"SET n += row.props",
            rows=rows,
        )

    def seed_agents_batch(self, items: list[dict[str, Any]]) -> None:
        """Batch-create seed Agent, AgentVersion, HAS_VERSION, USES_TOOL, and DELEGATES_TO in ONE managed write transaction."""
        if not items:
            return

        def _tx(tx: ManagedTransaction) -> None:
            existing_res = tx.run("MATCH (a:Agent) RETURN a.name AS name")
            existing = {r["name"] for r in existing_res}
            to_create = [it for it in items if it["name"] not in existing]
            if not to_create:
                return
            tx.run(
                f"UNWIND $items AS it "
                f"CREATE (a:Agent:{_HELPER}) SET a = it.agent_props, a.id = it.agent_id "
                f"CREATE (v:AgentVersion:{_HELPER}) SET v = it.version_props, v.id = it.version_id "
                f"CREATE (a)-[hv:HAS_VERSION {{id: it.hv_id}}]->(v) "
                f"WITH v, it "
                f"FOREACH (t IN it.tool_steps | "
                f"  MERGE (tl:Tool:{_HELPER} {{name: t.name}}) "
                f"  CREATE (v)-[:USES_TOOL {{id: t.rel_id, order: t.order}}]->(tl) "
                f")",
                items=to_create,
            )
            delegations = [
                {"ver_id": it["version_id"], "sub_agent": d["name"], "sub_ver": d["version"],
                 "rel_id": d["rel_id"], "order": d["order"]}
                for it in to_create
                for d in it["agent_steps"]
            ]
            if delegations:
                tx.run(
                    f"UNWIND $dels AS d "
                    f"MATCH (v:AgentVersion:{_HELPER} {{id: d.ver_id}}) "
                    f"MATCH (sub:AgentVersion:{_HELPER} {{agent: d.sub_agent, version: d.sub_ver}}) "
                    f"CREATE (v)-[:DELEGATES_TO {{id: d.rel_id, order: d.order}}]->(sub)",
                    dels=delegations,
                )

        self._exec_managed(write=True, fn=_tx)

    def create_node(self, label: str, props: Props) -> Node:
        check_label(label)
        check_props(props)
        rows = self._write(
            f"CREATE (n:{label}:{_HELPER}) SET n = $props, n.id = $id RETURN {_NODE_RET}",
            props=props, id=self._new_id(),
        )
        return _node(rows[0])

    def get_node(self, node_id: str) -> Node | None:
        rows = self._read(f"MATCH (n:{_HELPER} {{id: $id}}) RETURN {_NODE_RET}", id=node_id)
        return _node(rows[0]) if rows else None

    def find_nodes(self, label: str, match: Mapping[str, Scalar] | None = None) -> list[Node]:
        check_label(label)
        rows = self._read(
            f"MATCH (n:{label}) WHERE all(k IN keys($m) WHERE n[k] = $m[k]) RETURN {_NODE_RET}",
            m=dict(match or {}),
        )
        return [_node(r) for r in rows]

    def set_props(self, node_id: str, props: Props) -> Node:
        check_props(props)
        if _ALL_IMMUTABLE_KEYS & set(props):
            node = self.get_node(node_id)
            if node is None:
                raise GraphError(f"no node {node_id}")
            check_mutation(node.label, props)
        rows = self._write(
            f"MATCH (n:{_HELPER} {{id: $id}}) SET n += $props RETURN {_NODE_RET}",
            id=node_id, props=props,
        )
        if not rows:
            raise GraphError(f"no node {node_id}")
        return _node(rows[0])

    def create_rel(self, rel_type: str, start: str, end: str, props: Props | None = None) -> Rel:
        check_rel_type(rel_type)
        check_props(props or {})
        rows = self._write(
            f"MATCH (a:{_HELPER} {{id: $start}}), (b:{_HELPER} {{id: $end}}) "
            f"CREATE (a)-[r:{rel_type}]->(b) SET r = $props, r.id = $id RETURN {_REL_RET}",
            start=start, end=end, props=dict(props or {}), id=self._new_id(),
        )
        if not rows:
            raise GraphError(f"no node {start} or {end}")
        return _rel(rows[0])

    def set_rel_props(self, rel_id: str, props: Props) -> Rel:
        check_props(props)
        rows = self._write(
            f"MATCH (a)-[r {{id: $id}}]->(b) SET r += $props RETURN {_REL_RET}",
            id=rel_id, props=props,
        )
        if not rows:
            raise GraphError(f"no relationship {rel_id}")
        return _rel(rows[0])

    def rels(self, start: str | None = None, rel_type: str | None = None, end: str | None = None) -> list[Rel]:
        if rel_type is not None:
            check_rel_type(rel_type)
        rows = self._read(
            f"MATCH (a:{_HELPER})-[r]->(b:{_HELPER}) "
            "WHERE ($start IS NULL OR a.id = $start) AND ($end IS NULL OR b.id = $end) "
            "AND ($type IS NULL OR type(r) = $type) "
            f"RETURN {_REL_RET}",
            start=start, end=end, type=rel_type,
        )
        return [_rel(r) for r in rows]

    def all_nodes(self) -> list[Node]:
        return [_node(r) for r in self._read(f"MATCH (n:{_HELPER}) RETURN {_NODE_RET}")]

    def all_rels(self) -> list[Rel]:
        return self.rels()
