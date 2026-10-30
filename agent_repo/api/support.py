"""Plain, HTTP-free helpers shared by the local API.

This module imports only `agent_repo` core modules, so it can be unit-tested without FastAPI
installed. Two private CLI helpers are reused deliberately so the API cannot drift from the CLI:

- `agent_repo.cli._safe_text` — regex scrubbing plus exact configured-secret redaction, applied
  to every displayed string;
- `agent_repo.cli._make_store` — the same graph-file vs AuraDB selection the CLI uses.

Nothing here enables a real LLM or tool provider, and nothing here mutates governance state.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agent_repo.actors import NotHumanError
from agent_repo.cli import CLIError, _make_store, _safe_text
from agent_repo.config import Settings
from agent_repo.execution.runner import ExecutionResult
from agent_repo.feedback import FeedbackError
from agent_repo.graph.store import GraphError, GraphStore
from agent_repo.graph.views import execution_subgraph, render_execution_subgraph
from agent_repo.llm import LLMError
from agent_repo.manifest import RiskTier
from agent_repo.privacy import scrub
from agent_repo.repository import AgentRepository, RepositoryError
from agent_repo.routing import Plan
from agent_repo.seeds import seed_repository
from agent_repo.tools import ToolError, builtin_registry

SEED_MAINTAINER = "API Seed Maintainer"

# The same exception set the CLI boundary treats as an expected, user-facing error.
_EXPECTED_ERRORS = (
    CLIError, FeedbackError, RepositoryError, GraphError, NotHumanError, ValidationError,
    ValueError, OSError, KeyError, ToolError, LLMError,
)


def safe(text: object, settings: Settings) -> str:
    """Apply the CLI's scrubbing and configured-secret redaction to one displayed string."""
    return _safe_text(str(text), settings)


def describe_error(exc: BaseException, settings: Settings) -> str:
    """Format an error exactly as the CLI boundary does (never a raw stack trace)."""
    if isinstance(exc, _EXPECTED_ERRORS):
        return f"Error: {safe(str(exc), settings)}"
    return f"Error: {type(exc).__name__}: {safe(str(exc), settings)}"


def startup_error(exc: BaseException) -> str:
    """Sanitized error for the case where `Settings` itself could not be built."""
    return f"Error: {scrub(str(exc))}"


def target_caption(settings: Settings) -> str:
    """A credential-free statement of which backend the CLI-style selection will use."""
    if settings.neo4j is not None:
        return "Neo4j: configured (Aura) - credentials are never displayed."
    return "Neo4j: not configured - using the in-memory graph."


def make_store(settings: Settings, graph_file: str) -> tuple[GraphStore, Path | None]:
    """Select a store the way the CLI does: a graph file, else Aura when configured, else memory."""
    path = Path(graph_file).expanduser() if graph_file.strip() else None
    store, _uses_neo4j = _make_store(settings, path)
    return store, path


def make_repo(store: GraphStore, settings: Settings) -> AgentRepository:
    """Build the repository with the CLI's tool settings and seed it when it is empty."""
    repo = AgentRepository(store, tools=builtin_registry(tool_settings=settings.tools))
    if not repo.list_agents():
        seed_repository(repo, SEED_MAINTAINER)
    return repo


def model_label(repo: AgentRepository, agent: str, version: str) -> str:
    """`provider:model`, explicitly marked `MOCK LLM` for the offline provider."""
    manifest = repo.get_version(agent, version).manifest
    label = f"{manifest.provider}:{manifest.model}"
    return f"{label} (MOCK LLM)" if manifest.provider == "mock" else label


def plan_header(plan: Plan, settings: Settings) -> dict[str, Any]:
    """Plan-level fields. Values the plan did not record are returned as ``None`` (not invented)."""
    return {
        "query_type": _opt(plan.query_type_key, settings),
        "payload": plan.payload_modality.value,
        "goal": _opt(f"{plan.goal_agent}@{plan.goal_version}", settings),
        "intent_terms": _opt_list(plan.intent_tokens, settings),
        "dropped_modality_words": _opt_join(plan.dropped_modality_words, settings),
        "reused_workflow": _opt(plan.reused_workflow_id, settings),
        "alternatives": len(plan.alternatives),
    }


def plan_step_rows(repo: AgentRepository, plan: Plan, settings: Settings) -> list[dict[str, Any]]:
    """One display row per plan step, including why it was chosen and any stub/model labels.

    Honest about absence: if a field was not recorded on the stored plan step, the value is
    ``None`` rather than an invented default. Use ``reason``/``matched_terms``/``score`` straight
    from the step the CLI prints.
    """
    rows: list[dict[str, Any]] = []
    for step in plan.steps:
        try:
            model: str | None = model_label(repo, step.agent, step.version)
        except RepositoryError:
            model = None
        rows.append({
            "order": step.order + 1,
            "role": _opt(step.role, settings),
            "agent": _opt(step.agent, settings),
            "version": _opt(step.version, settings),
            "what_it_does": _opt(step.purpose, settings),
            "why_chosen": _opt(step.reason, settings),
            "matched_terms": _opt_list(step.matched_terms, settings),
            "score": None if step.score is None else float(step.score),
            "how_it_helps": _opt(step.how_it_helps, settings),
            "input": step.input_modality.value,
            "output": step.output_modality.value,
            "tools": [_opt(tool, settings) for tool in step.tools],
            "model": _opt(model, settings),
        })
    return rows


def result_view(repo: AgentRepository, result: ExecutionResult, settings: Settings) -> dict[str, Any]:
    """Structured, scrubbed view of one `ExecutionResult`."""
    output = ""
    if result.output is not None:
        output = result.output.text or json.dumps(result.output.data or {}, sort_keys=True)
    return {
        "status": result.status,
        "execution_id": result.execution_id,
        "workflow_id": result.workflow_id or "",
        "break_reason": result.break_reason.value if result.break_reason else "",
        "user_message": _opt(result.user_message, settings),
        "clarifying_question": _opt(result.clarifying_question, settings),
        "output": safe(output, settings),
        "proposal_id": result.proposal_id or "",
        "agent_request_id": result.agent_request_id or "",
        "steps_taken": int(result.steps_taken),
        "tokens_used": int(result.tokens_used),
        "elapsed_seconds": round(float(result.elapsed_seconds), 4),
        "plan": plan_header(result.plan, settings) if result.plan else None,
        "steps": plan_step_rows(repo, result.plan, settings) if result.plan else [],
    }


def agent_rows(repo: AgentRepository, settings: Settings) -> list[dict[str, Any]]:
    """Catalog rows: name, version, status, I/O, risk tools, sub-agents, example queries."""
    rows: list[dict[str, Any]] = []
    for name in repo.list_agents():
        description = repo.describe(name)
        risk_tools = [tool for tool in repo.reachable_tools(description.name, description.version)
                      if tool.risk is not RiskTier.LOW]
        risk_text = ", ".join(
            f"{tool.tool} ({tool.risk.value}{', STUB' if tool.is_stub else ''})"
            for tool in risk_tools
        ) or "none"
        rows.append({
            "name": safe(description.name, settings),
            "version": safe(description.version, settings),
            "status": safe(description.status, settings),
            "input -> output": f"{', '.join(description.input_modalities)} -> {description.output_type}",
            "risk tools": safe(risk_text, settings),
            "sub-agents": safe(", ".join(description.sub_agents) or "none", settings),
            "example queries": safe("; ".join(description.example_queries), settings),
        })
    return rows


def agent_detail(repo: AgentRepository, name: str, settings: Settings) -> dict[str, Any]:
    description = repo.describe(name)
    return {
        "name": safe(description.name, settings),
        "version": safe(description.version, settings),
        "status": safe(description.status, settings),
        "purpose": safe(description.purpose, settings),
        "usefulness": safe(description.usefulness, settings),
        "input": safe(f"{', '.join(description.input_modalities)} - {description.input_description}", settings),
        "processing": safe(description.processing_description, settings),
        "output": safe(f"{description.output_type} - {description.output_description}", settings),
        "model": safe(model_label(repo, description.name, description.version), settings),
        "chainable": description.chainable,
        "pipeline": [safe(item, settings) for item in description.pipeline],
        "tools": [safe(item, settings) for item in description.reachable_tools],
        "sub_agents": [safe(item, settings) for item in description.sub_agents],
        "examples": [safe(item, settings) for item in description.example_queries],
    }


def version_rows(repo: AgentRepository, agent: str, settings: Settings) -> list[dict[str, Any]]:
    default = repo.default_version(agent)
    return [
        {
            "version": safe(record.version, settings),
            "status": safe(record.status.value, settings),
            "author": safe(record.author, settings),
            "created at": safe(record.created_at, settings),
            "default": "yes" if default and default.version == record.version else "",
            "change note": safe(record.change_note, settings),
        }
        for record in repo.versions(agent)
    ]


def lineage_nodes_edges(repo: AgentRepository, agent: str, settings: Settings
                        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Nodes and SUPERSEDES edges for one agent's versions (oldest version is the chain root)."""
    records = repo.versions(agent)
    by_id = {record.node_id: record for record in records}
    nodes = [
        {
            "id": record.version,
            "label": safe(f"{record.version}\\n{record.status.value}", settings),
            "status": record.status.value,
            "author": safe(record.author, settings),
        }
        for record in records
    ]
    edges: list[dict[str, Any]] = []
    for record in records:
        for rel in repo.graph.rels(start=record.node_id, rel_type="SUPERSEDES"):
            parent = by_id.get(rel.end)
            if parent is not None:
                edges.append({"child": record.version, "parent": parent.version})
    return nodes, edges


def lineage_dot(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> str:
    """Deterministic Graphviz DOT source for the lineage graph."""
    lines = ["digraph lineage {", '  rankdir="LR";', '  node [shape="box"];']
    for node in nodes:
        label = str(node["label"]).replace('"', "'")
        lines.append(f'  "{node["id"]}" [label="{label}"];')
    for edge in edges:
        lines.append(f'  "{edge["child"]}" -> "{edge["parent"]}" [label="SUPERSEDES"];')
    lines.append("}")
    return "\n".join(lines)


def lineage_rows(repo: AgentRepository, agent: str, settings: Settings) -> list[dict[str, Any]]:
    """Table fallback for lineage: each version and its full SUPERSEDES chain."""
    rows: list[dict[str, Any]] = []
    for record in repo.versions(agent):
        chain = repo.lineage(agent, record.version)
        rows.append({
            "version": safe(record.version, settings),
            "status": safe(record.status.value, settings),
            "chain": safe(" <- ".join(f"{item.version} ({item.status.value})" for item in chain), settings),
        })
    return rows


def execution_rows(repo: AgentRepository, settings: Settings, limit: int = 25) -> list[dict[str, Any]]:
    """Recent executions, newest first."""
    nodes = sorted(
        repo.graph.find_nodes("Execution"),
        key=lambda node: str(node.props.get("created_at", "")),
        reverse=True,
    )[:limit]
    return [
        {
            "execution": node.id,
            "status": safe(str(node.props.get("status", "")), settings),
            "breakpoint": safe(str(node.props.get("break_reason", "")) or "none", settings),
            "query": safe(str(node.props.get("query", ""))[:100], settings),
            "created at": safe(str(node.props.get("created_at", "")), settings),
        }
        for node in nodes
    ]


def explain_text(repo: AgentRepository, execution_id: str, settings: Settings) -> str:
    """Exactly the text the CLI `explain` command renders, scrubbed."""
    return safe(render_execution_subgraph(execution_subgraph(repo, execution_id)), settings)


def proposal_rows(repo: AgentRepository, settings: Settings) -> list[dict[str, Any]]:
    """Proposals with their stored evaluation result; read-only."""
    rows: list[dict[str, Any]] = []
    nodes = sorted(
        repo.graph.find_nodes("Proposal"),
        key=lambda node: str(node.props.get("created_at", "")),
        reverse=True,
    )
    for node in nodes:
        props = node.props
        evaluation: dict[str, Any] = {}
        raw = props.get("evaluation_json")
        if raw:
            try:
                parsed = json.loads(str(raw))
                if isinstance(parsed, dict):
                    evaluation = parsed
            except json.JSONDecodeError:
                evaluation = {}
        rows.append({
            "proposal": node.id,
            "status": safe(str(props.get("status", "")), settings),
            "agent": safe(str(props.get("agent", "")), settings),
            "base version": safe(str(props.get("base_version", "")), settings),
            "candidate version": safe(str(props.get("candidate_version", "")) or "none", settings),
            "trigger": safe(str(props.get("trigger", "")), settings),
            "created at": safe(str(props.get("created_at", "")), settings),
            "evaluation": evaluation,
        })
    return rows


def evaluation_summary(evaluation: dict[str, Any], settings: Settings) -> str:
    if not evaluation:
        return "not evaluated"
    return safe(
        f"accuracy {evaluation.get('baseline_accuracy')} -> {evaluation.get('candidate_accuracy')}; "
        f"regressions={evaluation.get('regressions', [])}; "
        f"latency_ok={evaluation.get('latency_ok')}; "
        f"token_budget_ok={evaluation.get('token_budget_ok')}",
        settings,
    )


def _opt(text: object, settings: Settings) -> str | None:
    """Scrub a value, or ``None`` when it was not recorded (empty/whitespace)."""
    if text is None:
        return None
    cleaned = safe(text, settings).strip()
    return cleaned or None


def _opt_join(items: object, settings: Settings) -> str | None:
    """Scrub a list into a comma string, or ``None`` when the list is empty/absent."""
    if not items:
        return None
    joined = ", ".join(str(item) for item in items)  # type: ignore[union-attr]
    return _opt(joined, settings)


def _opt_list(items: object, settings: Settings) -> list[str] | None:
    """Return a list of scrubbed strings, or ``None`` when the list is empty/absent."""
    if not items:
        return None
    return [_opt(str(item), settings) for item in items]  # type: ignore[union-attr]


def _opt_list(items: object, settings: Settings) -> list[str] | None:
    """Scrub a list into a list of strings, or ``None`` when the list is empty/absent."""
    if not items:
        return None
    return [_opt(str(item), settings) or "" for item in items]  # type: ignore[union-attr]


@contextmanager
def temp_upload(file_name: str, data: bytes) -> Iterator[str]:
    """Write an uploaded file to a temp path and delete it when the block exits."""
    suffix = Path(file_name).suffix
    handle, path = tempfile.mkstemp(prefix="agent-repo-upload-", suffix=suffix)
    try:
        with os.fdopen(handle, "wb") as opened:
            opened.write(data)
        yield path
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
