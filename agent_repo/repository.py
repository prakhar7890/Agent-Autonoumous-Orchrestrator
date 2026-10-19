"""Agent repository: versioning rules enforced in code (spec §5) plus describe()/catalog (spec §3).

Rules enforced here:
- Manifests are immutable; every change is a new AgentVersion linked by SUPERSEDES, with a
  content hash and an integrity check.
- No hard delete: "delete" is the `deprecated` status.
- Status lifecycle candidate -> pending_approval -> stable -> deprecated, plus terminal rejected.
- Only a named human (naming convention, NOT authentication) may promote to stable, deprecate,
  or change an agent's default version.
- Non-human authors (llm*/system*) cannot create an agent's first version, change its
  input/output modalities, or introduce a HIGH-risk tool directly or through a sub-agent.
- Every new version starts as `candidate`; nothing can be created directly as stable.
- Approving a later version never changes the agent's default version.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel

from agent_repo.actors import is_human_name, require_human
from agent_repo.graph.store import GraphStore, Node
from agent_repo.manifest import AgentManifest, RiskTier, Status
from agent_repo.privacy import scrub, scrub_obj
from agent_repo.tools import ToolRegistry


class RepositoryError(Exception):
    pass


class GuardrailError(RepositoryError):
    pass


class TransitionError(RepositoryError):
    pass


ALLOWED_TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.CANDIDATE: frozenset({Status.PENDING_APPROVAL, Status.REJECTED}),
    Status.PENDING_APPROVAL: frozenset({Status.STABLE, Status.REJECTED}),
    Status.STABLE: frozenset({Status.DEPRECATED}),
    Status.DEPRECATED: frozenset(),
    Status.REJECTED: frozenset(),
}
_HUMAN_ONLY = frozenset({Status.STABLE, Status.DEPRECATED})


@dataclass(frozen=True)
class AgentVersionRecord:
    node_id: str
    agent: str
    version: str
    version_seq: int
    status: Status
    author: str
    manifest: AgentManifest
    content_hash: str
    created_at: str
    change_note: str


@dataclass(frozen=True)
class ReachableTool:
    tool: str
    risk: RiskTier
    is_stub: bool
    via: str  # "agent@version" that owns the tool step


@dataclass(frozen=True)
class IntegrityIssue:
    agent: str
    version: str
    problem: str


class AgentDescription(BaseModel):
    name: str
    version: str
    status: str
    purpose: str
    usefulness: str
    input_modalities: list[str]
    input_description: str
    processing_description: str
    pipeline: list[str]
    system_prompt: str
    output_type: str
    output_description: str
    example_queries: list[str]
    chainable: bool
    provider: str
    model: str
    own_tools: list[str]
    sub_agents: list[str]
    reachable_tools: list[str]  # "tool (risk[, STUB]) via agent@version", includes sub-agent tools


def _hash_text(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AgentRepository:
    def __init__(self, graph: GraphStore, tools: ToolRegistry,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.graph = graph
        self.tools = tools
        self._clock = clock
        self._sync_tools()

    # ------------------------------------------------------------------ tools
    def _sync_tools(self) -> None:
        tools_props = [
            {"name": spec.name, "description": spec.description, "risk": spec.risk.value,
             "is_stub": spec.is_stub, "input_modalities": [m.value for m in spec.input_modalities],
             "output_modality": spec.output_modality.value}
            for spec in self.tools.specs()
        ]
        batch = getattr(self.graph, "sync_tools_batch", None)
        if callable(batch):
            batch(tools_props)
            return
        for props in tools_props:
            existing = self.graph.find_nodes("Tool", {"name": props["name"]})
            if existing:
                self.graph.set_props(existing[0].id, props)
            else:
                self.graph.create_node("Tool", props)

    def _tool_node(self, name: str) -> Node:
        nodes = self.graph.find_nodes("Tool", {"name": name})
        if not nodes:
            raise RepositoryError(f"unknown tool {name!r}")
        return nodes[0]

    # ------------------------------------------------------------------ reads
    def _agent_node(self, name: str) -> Node | None:
        nodes = self.graph.find_nodes("Agent", {"name": name})
        return nodes[0] if nodes else None

    def _require_agent(self, name: str) -> Node:
        node = self._agent_node(name)
        if node is None:
            raise RepositoryError(f"unknown agent {name!r}")
        return node

    @staticmethod
    def _record(node: Node) -> AgentVersionRecord:
        p = node.props
        return AgentVersionRecord(node.id, p["agent"], p["version"], int(p["version_seq"]), Status(p["status"]),
                                  p["author"], AgentManifest.from_json(p["manifest_json"]), p["content_hash"],
                                  p["created_at"], p.get("change_note", ""))

    def list_agents(self) -> list[str]:
        return sorted(n.props["name"] for n in self.graph.find_nodes("Agent"))

    def versions(self, agent: str) -> list[AgentVersionRecord]:
        self._require_agent(agent)
        nodes = self.graph.find_nodes("AgentVersion", {"agent": agent})
        return sorted((self._record(n) for n in nodes), key=lambda r: r.version_seq)

    def get_version(self, agent: str, version: str) -> AgentVersionRecord:
        nodes = self.graph.find_nodes("AgentVersion", {"agent": agent, "version": version})
        if not nodes:
            raise RepositoryError(f"unknown version {agent}@{version}")
        return self._record(nodes[0])

    def default_version(self, agent: str) -> AgentVersionRecord | None:
        version = self._require_agent(agent).props.get("default_version")
        return self.get_version(agent, version) if version else None

    def lineage(self, agent: str, version: str) -> list[AgentVersionRecord]:
        """Follow SUPERSEDES from `version` back to the first version."""
        chain = [self.get_version(agent, version)]
        while True:
            parents = self.graph.rels(start=chain[-1].node_id, rel_type="SUPERSEDES")
            if not parents:
                return chain
            node = self.graph.get_node(parents[0].end)
            assert node is not None
            chain.append(self._record(node))

    def reachable_tools(self, agent: str, version: str) -> list[ReachableTool]:
        """Tools used by this version or transitively by its pinned sub-agents."""
        found: list[ReachableTool] = []
        seen: set[tuple[str, str]] = set()
        stack = [(agent, version)]
        while stack:
            key = stack.pop()
            if key in seen:
                continue
            seen.add(key)
            manifest = self.get_version(*key).manifest
            for step in manifest.pipeline:
                if step.kind == "tool":
                    spec = self.tools.get(step.name)
                    found.append(ReachableTool(step.name, spec.risk, spec.is_stub, f"{key[0]}@{key[1]}"))
                else:
                    stack.append((step.name, step.version or ""))
        return found

    def reaches_high_risk(self, agent: str, version: str) -> bool:
        return any(t.risk is RiskTier.HIGH for t in self.reachable_tools(agent, version))

    # ------------------------------------------------------------------ writes
    def create_agent(self, manifest: AgentManifest, author: str, change_note: str = "initial version"
                     ) -> AgentVersionRecord:
        author = require_human(author, "creating the first version of an agent")
        if self._agent_node(manifest.name) is not None:
            raise RepositoryError(f"agent {manifest.name!r} already exists; create a new version instead")
        self._check_manifest(manifest, author)
        agent = self.graph.create_node("Agent", {"name": manifest.name, "created_by": author,
                                                 "created_at": self._now(), "default_version": None})
        return self._create_version_node(agent, manifest, author, 1, None, change_note)

    def create_version(self, agent_name: str, manifest: AgentManifest, author: str,
                       change_note: str, parent_version: str | None = None) -> AgentVersionRecord:
        """Create a new candidate version superseding `parent_version` (default: latest)."""
        if not author or not author.strip():
            raise RepositoryError("author is required")
        agent = self._require_agent(agent_name)
        if manifest.name != agent_name:
            raise RepositoryError("manifest name does not match agent")
        history = self.versions(agent_name)
        parent = self.get_version(agent_name, parent_version) if parent_version else history[-1]
        if manifest.content_hash() == parent.content_hash:
            raise RepositoryError("manifest is identical to its parent; nothing to version")
        if not is_human_name(author):
            if (manifest.input_modalities != parent.manifest.input_modalities
                    or manifest.output_type != parent.manifest.output_type):
                raise GuardrailError("non-human authors cannot change input/output modalities")
        self._check_manifest(manifest, author)
        return self._create_version_node(agent, manifest, author, history[-1].version_seq + 1, parent,
                                         change_note)

    def _check_manifest(self, manifest: AgentManifest, author: str) -> None:
        if scrub_obj(manifest.model_dump(mode="json")) != manifest.model_dump(mode="json"):
            raise GuardrailError("manifest contains secret- or PII-like content; remove it before storing")
        for step in manifest.pipeline:
            if step.kind == "tool" and not self.tools.has(step.name):
                raise RepositoryError(f"unknown tool {step.name!r}")
            if step.kind == "agent":
                sub = self.get_version(step.name, step.version or "")
                if sub.status is not Status.STABLE:
                    raise RepositoryError(f"sub-agent {step.name}@{step.version} is {sub.status.value}, not stable")
        if not is_human_name(author):
            for step in manifest.pipeline:
                if step.kind == "tool" and self.tools.get(step.name).risk is RiskTier.HIGH:
                    raise GuardrailError(f"non-human authors cannot add HIGH-risk tool {step.name!r}")
                if step.kind == "agent" and self.reaches_high_risk(step.name, step.version or ""):
                    raise GuardrailError(
                        f"non-human authors cannot add sub-agent {step.name}@{step.version}: it reaches a HIGH-risk tool")

    def _create_version_node(self, agent: Node, manifest: AgentManifest, author: str, seq: int,
                             parent: AgentVersionRecord | None, change_note: str) -> AgentVersionRecord:
        raw = manifest.canonical_json()
        node = self.graph.create_node("AgentVersion", {
            "agent": manifest.name, "version": f"0.{seq}", "version_seq": seq, "status": Status.CANDIDATE.value,
            "author": author, "change_note": scrub(change_note), "manifest_json": raw,
            "content_hash": _hash_text(raw), "created_at": self._now(),
            "purpose": manifest.purpose, "provider": manifest.provider, "model": manifest.model,
        })
        self.graph.create_rel("HAS_VERSION", agent.id, node.id)
        if parent is not None:
            self.graph.create_rel("SUPERSEDES", node.id, parent.node_id)
        for order, step in enumerate(manifest.pipeline):
            if step.kind == "tool":
                self.graph.create_rel("USES_TOOL", node.id, self._tool_node(step.name).id, {"order": order})
            else:
                sub = self.get_version(step.name, step.version or "")
                self.graph.create_rel("DELEGATES_TO", node.id, sub.node_id, {"order": order})
        return self._record(node)

    # ------------------------------------------------------------------ status
    def transition(self, agent: str, version: str, to: Status, actor: str, reason: str = "") -> AgentVersionRecord:
        rec = self.get_version(agent, version)
        if to not in ALLOWED_TRANSITIONS[rec.status]:
            raise TransitionError(f"illegal transition {rec.status.value} -> {to.value} for {agent}@{version}")
        if to in _HUMAN_ONLY:
            actor = require_human(actor, f"moving a version to {to.value}")
        elif not actor or not actor.strip():
            raise TransitionError("actor is required")
        self.graph.set_props(rec.node_id, {"status": to.value, f"{to.value}_by": actor.strip(),
                                           f"{to.value}_at": self._now(), "status_reason": scrub(reason)})
        if to is Status.STABLE:
            agent_node = self._require_agent(agent)
            if not agent_node.props.get("default_version"):
                self.graph.set_props(agent_node.id, {"default_version": version})
        return self.get_version(agent, version)

    def submit_for_approval(self, agent: str, version: str, actor: str) -> AgentVersionRecord:
        return self.transition(agent, version, Status.PENDING_APPROVAL, actor)

    def approve(self, agent: str, version: str, approver: str) -> AgentVersionRecord:
        return self.transition(agent, version, Status.STABLE, approver)

    def reject(self, agent: str, version: str, actor: str, reason: str) -> AgentVersionRecord:
        return self.transition(agent, version, Status.REJECTED, actor, reason)

    def deprecate(self, agent: str, version: str, actor: str, reason: str) -> AgentVersionRecord:
        """The only form of "delete": the version stays stored and traceable."""
        return self.transition(agent, version, Status.DEPRECATED, actor, reason)

    def set_default_version(self, agent: str, version: str, actor: str) -> None:
        actor = require_human(actor, "changing an agent's default version")
        if self.get_version(agent, version).status is not Status.STABLE:
            raise RepositoryError("default version must be stable")
        self.graph.set_props(self._require_agent(agent).id, {"default_version": version})

    # ------------------------------------------------------------------ integrity
    def verify_integrity(self) -> list[IntegrityIssue]:
        issues: list[IntegrityIssue] = []
        for node in self.graph.find_nodes("AgentVersion"):
            p = node.props
            raw = p.get("manifest_json", "")
            if _hash_text(raw) != p.get("content_hash"):
                issues.append(IntegrityIssue(p.get("agent", "?"), p.get("version", "?"), "content hash mismatch"))
                continue
            try:
                AgentManifest.from_json(raw)
            except ValueError as exc:
                issues.append(IntegrityIssue(p["agent"], p["version"], f"manifest invalid: {exc}"))
        return issues

    # ------------------------------------------------------------------ describe / catalog
    def describe(self, agent: str, version: str | None = None) -> AgentDescription:
        if version is not None:
            rec = self.get_version(agent, version)
        else:
            rec = self.default_version(agent) or self.versions(agent)[-1]
        m = rec.manifest
        reach = self.reachable_tools(rec.agent, rec.version)
        return AgentDescription(
            name=m.name, version=rec.version, status=rec.status.value, purpose=m.purpose,
            usefulness=m.usefulness, input_modalities=[x.value for x in m.input_modalities],
            input_description=m.input_description, processing_description=m.processing_description,
            pipeline=[f"{i + 1}. {s.kind} {s.name}{'@' + s.version if s.version else ''} {s.params or ''}".rstrip()
                      for i, s in enumerate(m.pipeline)],
            system_prompt=m.system_prompt, output_type=m.output_type.value, output_description=m.output_description,
            example_queries=list(m.example_queries), chainable=m.chainable, provider=m.provider, model=m.model,
            own_tools=m.tool_names(), sub_agents=[f"{n}@{v}" for n, v in m.sub_agents()],
            reachable_tools=[f"{t.tool} ({t.risk.value}{', STUB' if t.is_stub else ''}) via {t.via}" for t in reach],
        )

    def catalog_markdown(self) -> str:
        lines = ["# Agent Catalog", "", "_Generated from the graph. STUB marks tools that are deterministic "
                 "placeholders, not real models._", ""]
        for name in self.list_agents():
            d = self.describe(name)
            lines += [
                f"## {d.name} @ {d.version} ({d.status})", "",
                f"- **Purpose:** {d.purpose}", f"- **Why useful:** {d.usefulness}",
                f"- **Input:** {', '.join(d.input_modalities)} — {d.input_description}",
                f"- **Processing:** {d.processing_description}",
                f"- **Output:** {d.output_type} — {d.output_description}",
                f"- **Chainable prerequisite:** {'yes' if d.chainable else 'no'}",
                f"- **Model:** {d.provider}:{d.model}",
                f"- **Sub-agents:** {', '.join(d.sub_agents) or 'none'}",
                f"- **Tools (incl. via sub-agents):** {', '.join(d.reachable_tools) or 'none'}",
                "- **Pipeline:**", *[f"  - {p}" for p in d.pipeline],
                "- **Example queries:**", *[f"  - {q}" for q in d.example_queries], "",
            ]
        return "\n".join(lines)

    def _now(self) -> str:
        return self._clock().isoformat()
