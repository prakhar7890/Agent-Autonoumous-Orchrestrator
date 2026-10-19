"""GraphStore interface, shared schema/immutability checks, and the in-memory backend.

Both backends (InMemoryGraph, Neo4jGraph) enforce the same schema and immutability rules
through the helpers here, so business logic and tests are backend-independent.
There is deliberately NO delete operation: nothing is hard-deleted (spec §5).
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

NODE_LABELS = frozenset({
    "Agent", "AgentVersion", "Tool", "QueryType", "Workflow", "Execution",
    "Feedback", "Case", "Proposal", "Escalation", "AgentRequest",
})
REL_TYPES = frozenset({
    "HAS_VERSION", "SUPERSEDES", "USES_TOOL", "DELEGATES_TO", "PREFERS", "INCLUDES",
    "RAN", "OF_TYPE", "GOT", "HAS_CASE", "HAS_PROPOSAL", "PROPOSES", "ESCALATED", "REQUESTED",
})
# Properties that can never change once a node is created.
IMMUTABLE_PROPS: Mapping[str, frozenset[str]] = {
    "AgentVersion": frozenset({"manifest_json", "content_hash", "agent", "version", "version_seq",
                               "author", "created_at"}),
}
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

Scalar = str | int | float | bool | None
Props = dict[str, Any]


class GraphError(Exception):
    """Schema violation, unknown id, or attempted mutation of an immutable property."""


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    props: Props = field(default_factory=dict)


@dataclass(frozen=True)
class Rel:
    id: str
    type: str
    start: str
    end: str
    props: Props = field(default_factory=dict)


def check_label(label: str) -> None:
    if label not in NODE_LABELS:
        raise GraphError(f"unknown node label {label!r}")


def check_rel_type(rel_type: str) -> None:
    if rel_type not in REL_TYPES:
        raise GraphError(f"unknown relationship type {rel_type!r}")


def check_props(props: Mapping[str, Any]) -> None:
    """Neo4j-compatible property values: scalars or homogeneous lists of scalars."""
    for key, value in props.items():
        if not _KEY_RE.match(key) or key == "id":
            raise GraphError(f"invalid property key {key!r}")
        if isinstance(value, list):
            if any(isinstance(v, (list, dict)) for v in value):
                raise GraphError(f"property {key!r}: nested values must be stored as JSON strings")
        elif isinstance(value, dict):
            raise GraphError(f"property {key!r}: dicts must be stored as JSON strings")
        elif not isinstance(value, (str, int, float, bool)) and value is not None:
            raise GraphError(f"property {key!r}: unsupported type {type(value).__name__}")


def check_mutation(label: str, props: Mapping[str, Any]) -> None:
    frozen = IMMUTABLE_PROPS.get(label, frozenset()) & set(props)
    if frozen:
        raise GraphError(f"{label} properties are immutable: {sorted(frozen)}")


def new_id() -> str:
    return uuid.uuid4().hex


class GraphStore(Protocol):
    def create_node(self, label: str, props: Props) -> Node: ...
    def get_node(self, node_id: str) -> Node | None: ...
    def find_nodes(self, label: str, match: Mapping[str, Scalar] | None = None) -> list[Node]: ...
    def set_props(self, node_id: str, props: Props) -> Node: ...
    def create_rel(self, rel_type: str, start: str, end: str, props: Props | None = None) -> Rel: ...
    def set_rel_props(self, rel_id: str, props: Props) -> Rel: ...
    def rels(self, start: str | None = None, rel_type: str | None = None, end: str | None = None) -> list[Rel]: ...
    def all_nodes(self) -> list[Node]: ...
    def all_rels(self) -> list[Rel]: ...


class InMemoryGraph:
    """Dict-backed GraphStore, persistable to a JSON file."""

    def __init__(self, id_factory: Callable[[], str] = new_id) -> None:
        self._nodes: dict[str, Node] = {}
        self._rels: dict[str, Rel] = {}
        self._new_id = id_factory

    def create_node(self, label: str, props: Props) -> Node:
        check_label(label)
        check_props(props)
        node = Node(self._new_id(), label, dict(props))
        self._nodes[node.id] = node
        return node

    def get_node(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def find_nodes(self, label: str, match: Mapping[str, Scalar] | None = None) -> list[Node]:
        check_label(label)
        crit = dict(match or {})
        return [n for n in self._nodes.values()
                if n.label == label and all(n.props.get(k) == v for k, v in crit.items())]

    def set_props(self, node_id: str, props: Props) -> Node:
        node = self._require(node_id)
        check_props(props)
        check_mutation(node.label, props)
        updated = Node(node.id, node.label, {**node.props, **props})
        self._nodes[node_id] = updated
        return updated

    def create_rel(self, rel_type: str, start: str, end: str, props: Props | None = None) -> Rel:
        check_rel_type(rel_type)
        self._require(start)
        self._require(end)
        check_props(props or {})
        rel = Rel(self._new_id(), rel_type, start, end, dict(props or {}))
        self._rels[rel.id] = rel
        return rel

    def set_rel_props(self, rel_id: str, props: Props) -> Rel:
        rel = self._rels.get(rel_id)
        if rel is None:
            raise GraphError(f"no relationship {rel_id}")
        check_props(props)
        updated = Rel(rel.id, rel.type, rel.start, rel.end, {**rel.props, **props})
        self._rels[rel_id] = updated
        return updated

    def rels(self, start: str | None = None, rel_type: str | None = None, end: str | None = None) -> list[Rel]:
        return [r for r in self._rels.values()
                if (start is None or r.start == start)
                and (rel_type is None or r.type == rel_type)
                and (end is None or r.end == end)]

    def all_nodes(self) -> list[Node]:
        return list(self._nodes.values())

    def all_rels(self) -> list[Rel]:
        return list(self._rels.values())

    def save(self, path: Path) -> None:
        data = {
            "nodes": [{"id": n.id, "label": n.label, "props": n.props} for n in self._nodes.values()],
            "rels": [{"id": r.id, "type": r.type, "start": r.start, "end": r.end, "props": r.props}
                     for r in self._rels.values()],
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path, id_factory: Callable[[], str] = new_id) -> InMemoryGraph:
        graph = cls(id_factory)
        data = json.loads(path.read_text(encoding="utf-8"))
        for n in data["nodes"]:
            check_label(n["label"])
            check_props(n["props"])
            graph._nodes[n["id"]] = Node(n["id"], n["label"], n["props"])
        for r in data["rels"]:
            check_rel_type(r["type"])
            graph._rels[r["id"]] = Rel(r["id"], r["type"], r["start"], r["end"], r["props"])
        return graph

    def _require(self, node_id: str) -> Node:
        node = self._nodes.get(node_id)
        if node is None:
            raise GraphError(f"no node {node_id}")
        return node



