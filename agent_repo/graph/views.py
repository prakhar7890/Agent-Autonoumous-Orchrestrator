"""Graph views: execution_subgraph(execution_id), text renderer, and Cypher export (spec §4)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agent_repo.graph.store import GraphError, GraphStore
from agent_repo.repository import AgentRepository


@dataclass(frozen=True)
class SubgraphAgentStep:
    order: int
    role: str
    agent: str
    version: str
    purpose: str
    reason: str
    own_tools: list[str]
    sub_agents: list[str]
    reachable_tools: list[str]


@dataclass(frozen=True)
class ExecutionSubgraph:
    execution_id: str
    query: str
    status: str
    break_reason: str
    output_text: str
    query_type_key: str
    workflow_id: str | None
    steps: list[SubgraphAgentStep] = field(default_factory=list)
    escalation: dict[str, Any] | None = None
    agent_request: dict[str, Any] | None = None


def execution_subgraph(repo: AgentRepository, execution_id: str) -> ExecutionSubgraph:
    """Extract the execution subgraph: Execution, QueryType, Workflow, each AgentVersion with its
    stored reason on the INCLUDES edge, and all tools behind it (including tools of sub-agents).
    """
    g = repo.graph
    exec_node = g.get_node(execution_id)
    if exec_node is None or exec_node.label != "Execution":
        raise GraphError(f"no Execution node {execution_id!r}")

    ep = exec_node.props
    q_key = str(ep.get("query_type_key", "unmatched"))
    of_type_rels = g.rels(start=execution_id, rel_type="OF_TYPE")
    if of_type_rels:
        qt_node = g.get_node(of_type_rels[0].end)
        if qt_node is not None:
            q_key = str(qt_node.props.get("key", q_key))

    ran_rels = g.rels(start=execution_id, rel_type="RAN")
    wf_id: str | None = ran_rels[0].end if ran_rels else None
    steps: list[SubgraphAgentStep] = []

    if wf_id is not None:
        inc_rels = sorted(
            g.rels(start=wf_id, rel_type="INCLUDES"),
            key=lambda r: int(r.props.get("order", 0)),
        )
        for rel in inc_rels:
            ver_node = g.get_node(rel.end)
            if ver_node is None:
                continue
            agent_name = str(ver_node.props["agent"])
            ver_str = str(ver_node.props["version"])
            desc = repo.describe(agent_name, ver_str)
            steps.append(SubgraphAgentStep(
                order=int(rel.props.get("order", len(steps))),
                role=str(rel.props.get("role", "goal")),
                agent=agent_name,
                version=ver_str,
                purpose=desc.purpose,
                reason=str(rel.props.get("reason", "")),
                own_tools=desc.own_tools,
                sub_agents=desc.sub_agents,
                reachable_tools=desc.reachable_tools,
            ))

    esc_rels = g.rels(start=execution_id, rel_type="ESCALATED")
    esc_dict = dict(g.get_node(esc_rels[0].end).props) if esc_rels and g.get_node(esc_rels[0].end) else None

    req_rels = g.rels(start=execution_id, rel_type="REQUESTED")
    req_dict = dict(g.get_node(req_rels[0].end).props) if req_rels and g.get_node(req_rels[0].end) else None

    return ExecutionSubgraph(
        execution_id=execution_id,
        query=str(ep.get("query", "")),
        status=str(ep.get("status", "")),
        break_reason=str(ep.get("break_reason", "")),
        output_text=str(ep.get("output_text", "")),
        query_type_key=q_key,
        workflow_id=wf_id,
        steps=steps,
        escalation=esc_dict,
        agent_request=req_dict,
    )


def render_execution_subgraph(sub: ExecutionSubgraph) -> str:
    """Human-readable text renderer for an ExecutionSubgraph (spec §4)."""
    lines = [
        f"Execution: {sub.execution_id}",
        f"  Status:     {sub.status}" + (f" (break_reason={sub.break_reason})" if sub.break_reason else ""),
        f"  Query:      {sub.query}",
        f"  QueryType:  {sub.query_type_key}",
        f"  Workflow:   {sub.workflow_id or 'none'}",
    ]
    if sub.steps:
        lines.append("  Agents & Tools:")
        for s in sub.steps:
            lines.append(f"    [{s.order + 1}] {s.agent}@{s.version} (role={s.role})")
            lines.append(f"        Purpose:         {s.purpose}")
            lines.append(f"        Stored reason:   {s.reason}")
            if s.sub_agents:
                lines.append(f"        Sub-agents:      {', '.join(s.sub_agents)}")
            lines.append(f"        Reachable tools: {', '.join(s.reachable_tools) or 'none'}")
    if sub.output_text:
        lines.append(f"  Output:     {sub.output_text}")
    if sub.escalation:
        lines.append(f"  Escalation: reason={sub.escalation.get('reason')} | question={sub.escalation.get('clarifying_question')}")
    if sub.agent_request:
        lines.append(f"  Draft AgentRequest: status={sub.agent_request.get('status')} (routable={sub.agent_request.get('routable')})")
    return "\n".join(lines)


def _cypher_literal(val: Any) -> str:
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return repr(val)
    if isinstance(val, str):
        return json.dumps(val)
    if isinstance(val, list):
        return "[" + ", ".join(_cypher_literal(x) for x in val) + "]"
    return json.dumps(str(val))


def _cypher_props(props: dict[str, Any]) -> str:
    items = [f"{k}: {_cypher_literal(v)}" for k, v in sorted(props.items())]
    return "{" + ", ".join(items) + "}"


def export_cypher(graph: GraphStore) -> str:
    """Export the entire graph as a valid Cypher script loadable into Neo4j Browser / Aura Workspace."""
    lines = [
        "// Generated Cypher export from agent_repo",
        "CREATE CONSTRAINT graphnode_id IF NOT EXISTS FOR (n:GraphNode) REQUIRE n.id IS UNIQUE;",
    ]
    for node in sorted(graph.all_nodes(), key=lambda n: (n.label, n.id)):
        merged_props = {"id": node.id, **node.props}
        lines.append(f"MERGE (n:{node.label}:GraphNode {{id: {_cypher_literal(node.id)}}}) SET n += {_cypher_props(merged_props)};")
    for rel in sorted(graph.all_rels(), key=lambda r: (r.type, r.id)):
        merged_props = {"id": rel.id, **rel.props}
        lines.append(
            f"MATCH (a:GraphNode {{id: {_cypher_literal(rel.start)}}}), (b:GraphNode {{id: {_cypher_literal(rel.end)}}}) "
            f"MERGE (a)-[r:{rel.type} {{id: {_cypher_literal(rel.id)}}}]->(b) SET r += {_cypher_props(merged_props)};"
        )
    return "\n".join(lines) + "\n"

