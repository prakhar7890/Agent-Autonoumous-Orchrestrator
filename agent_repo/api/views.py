"""Transport-free view builders for the local API.

These functions convert core objects into the API's pydantic models. They reuse the exact
same helpers the CLI uses (`agent_repo.api.support`), which in turn reuse the CLI's
`_safe_text`/`_make_store`, so the API cannot drift from the CLI's scrubbing or backend
selection. Nothing here mutates governance state.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from agent_repo.config import Settings
from agent_repo.execution.runner import ExecutionResult
from agent_repo.graph.store import GraphStore
from agent_repo.manifest import Modality
from agent_repo.repository import AgentRepository
from agent_repo.api import support as api_support

from agent_repo.api.models import (
    AgentDetail,
    AgentRow,
    AskResponse,
    EvaluationResult,
    ExecutionDetail,
    ExecutionRow,
    LineageRow,
    PlanHeader,
    PlanStep,
    ProposalRow,
    TypedOutput,
    VersionRow,
)

# Default cap on the raw bytes embedded (base64) in a typed image output.
# Overridden by AGENT_REPO_API_MAX_IMAGE_BYTES via Settings.tools.max_image_bytes (default 4 MB).
DEFAULT_MAX_OUTPUT_BYTES = 2 * 1024 * 1024

_IMAGE_MIME = {
    "svg": "image/svg+xml",
    "png": "image/png",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "webp": "image/webp",
}


def typed_output(output: Any, settings: Settings, max_bytes: int | None = None) -> TypedOutput:
    """Convert a `Payload` (or None) into a typed output object.

    Image payloads are returned as base64 data with a mime type, subject to `max_bytes`. The
    bytes are never inlined into HTML and never persisted. Nothing is ever silently truncated:
    over the cap, a text explanation is returned instead.
    """
    if output is None:
        return TypedOutput(kind="text", text="")

    modality = getattr(output, "modality", None)
    data = getattr(output, "data", None) or {}
    text = getattr(output, "text", None)

    if modality is Modality.IMAGE:
        content = data.get("content")
        fmt = str(data.get("format", "")).lower()
        mime = _IMAGE_MIME.get(fmt, "application/octet-stream")
        caption = api_support.safe(text or "[image output]", settings)
        if content is None or (isinstance(content, str) and not content):
            return TypedOutput(kind="text", text=f"{caption} (image data unavailable)")
        if isinstance(content, str):
            raw = content.encode("utf-8")
        elif isinstance(content, (bytes, bytearray)):
            raw = bytes(content)
        else:
            return TypedOutput(kind="text", text=f"{caption} (image data unavailable)")
        max_output = max_bytes if max_bytes is not None else settings.tools.max_image_bytes
        if len(raw) > max_output:
            return TypedOutput(
                kind="text",
                text=f"{caption} (image output is {len(raw)} bytes, above the {max_output}-byte cap; "
                     "data not returned)",
            )
        return TypedOutput(
            kind="image",
            text=caption,
            mime=mime,
            data_base64=base64.b64encode(raw).decode("ascii"),
        )

    if modality is Modality.JSON:
        encoded = json.dumps(data, sort_keys=True)
        return TypedOutput(kind="json", text=api_support.safe(encoded, settings))

    return TypedOutput(kind="text", text=api_support.safe(text or "", settings))


def agent_rows(repo: AgentRepository, settings: Settings) -> list[AgentRow]:
    return [
        AgentRow(
            name=row["name"],
            version=row["version"],
            status=row["status"],
            input_to_output=row["input -> output"],
            risk_tools=row["risk tools"],
            sub_agents=row["sub-agents"],
            example_queries=row["example queries"],
        )
        for row in api_support.agent_rows(repo, settings)
    ]


def agent_detail(repo: AgentRepository, name: str, settings: Settings) -> AgentDetail:
    return AgentDetail(**api_support.agent_detail(repo, name, settings))


def version_rows(repo: AgentRepository, agent: str, settings: Settings) -> list[VersionRow]:
    default = repo.default_version(agent)
    return [
        VersionRow(
            version=row["version"],
            status=row["status"],
            author=row["author"],
            created_at=row["created at"],
            default=row["default"],
            change_note=row["change note"],
        )
        for row in api_support.version_rows(repo, agent, settings)
    ]


def lineage_rows(repo: AgentRepository, agent: str, settings: Settings) -> list[LineageRow]:
    return [LineageRow(**row) for row in api_support.lineage_rows(repo, agent, settings)]


def execution_rows(repo: AgentRepository, settings: Settings, limit: int) -> list[ExecutionRow]:
    return [
        ExecutionRow(
            execution=row["execution"],
            status=row["status"],
            breakpoint=row["breakpoint"],
            query=row["query"],
            created_at=row["created at"],
        )
        for row in api_support.execution_rows(repo, settings, limit)
    ]


def execution_detail(repo: AgentRepository, execution_id: str, settings: Settings) -> ExecutionDetail:
    """Same content as the CLI `explain`: rendered subgraph text plus stored typed output.

    If a media artifact is durably stored for this execution, its typed representation is loaded
    from the artifact store; otherwise stored scrubbed output_text is returned.
    """
    node = repo.graph.get_node(execution_id)
    if node is None or node.label != "Execution":
        raise KeyError(execution_id)
    stored = str(node.props.get("output_text", ""))
    output: TypedOutput | None = None
    artifact_id = str(node.props.get("artifact_id", ""))
    if artifact_id:
        from agent_repo.artifacts import get_default_artifact_store
        store = get_default_artifact_store(settings)
        try:
            raw, mime = store.load_image(
                artifact_id,
                expected_ref=str(node.props.get("artifact_ref", "")) or None,
            )
            output = TypedOutput(
                kind="image",
                text=api_support.safe(stored, settings) or "[image output]",
                mime=mime,
                data_base64=base64.b64encode(raw).decode("ascii"),
                model=str(node.props.get("artifact_model", "")) or None,
            )
        except Exception:  # noqa: BLE001
            pass
    if output is None:
        output = TypedOutput(kind="text", text=api_support.safe(stored, settings))
    return ExecutionDetail(
        execution_id=execution_id,
        rendered=api_support.explain_text(repo, execution_id, settings),
        output=output,
    )


def proposal_rows(repo: AgentRepository, settings: Settings) -> list[ProposalRow]:
    rows: list[ProposalRow] = []
    for row in api_support.proposal_rows(repo, settings):
        eval_raw = row["evaluation"] if isinstance(row["evaluation"], dict) else {}
        known = {"baseline_accuracy", "candidate_accuracy", "regressions", "latency_ok", "token_budget_ok"}
        evaluation = EvaluationResult(**{k: eval_raw[k] for k in eval_raw if k in known})
        rows.append(ProposalRow(
            proposal=row["proposal"],
            status=row["status"],
            agent=row["agent"],
            base_version=row["base version"],
            candidate_version=row["candidate version"],
            trigger=row["trigger"],
            created_at=row["created at"],
            evaluation=evaluation,
        ))
    return rows


def ask_response(repo: AgentRepository, result: ExecutionResult, settings: Settings,
                 max_bytes: int | None = None,
                 upload_filename: str | None = None) -> AskResponse:
    plan = result.plan
    header = None
    steps: list[PlanStep] = []
    if plan is not None:
        raw_header = api_support.plan_header(plan, settings)
        header = PlanHeader(
            query_type=raw_header["query_type"],
            payload=raw_header["payload"],
            goal=raw_header["goal"],
            intent_terms=raw_header["intent_terms"],
            dropped_modality_words=raw_header["dropped_modality_words"],
            reused_workflow=raw_header["reused_workflow"],
            alternatives=raw_header["alternatives"],
        )
        steps = [
            PlanStep(
                order=step["order"],
                role=step["role"],
                agent=step["agent"],
                version=step["version"],
                what_it_does=step["what_it_does"],
                why_chosen=step["why_chosen"],
                matched_terms=step["matched_terms"],
                score=step["score"],
                how_it_helps=step["how_it_helps"],
                input=step["input"],
                output=step["output"],
                tools=step["tools"],
                model=step["model"],
            )
            for step in api_support.plan_step_rows(repo, plan, settings)
        ]
    return AskResponse(
        status=result.status,
        execution_id=result.execution_id,
        workflow_id=result.workflow_id or "",
        break_reason=result.break_reason.value if result.break_reason else "",
        user_message=api_support.safe(result.user_message, settings),
        clarifying_question=api_support.safe(result.clarifying_question, settings),
        output=typed_output(result.output, settings, max_bytes),
        proposal_id=result.proposal_id or "",
        agent_request_id=result.agent_request_id or "",
        steps_taken=int(result.steps_taken),
        tokens_used=int(result.tokens_used),
        elapsed_seconds=round(float(result.elapsed_seconds), 4),
        upload_filename=upload_filename,
        plan=header,
        steps=steps,
    )


def build_repo(store: GraphStore, settings: Settings) -> AgentRepository:
    """Seed an empty store the same way the CLI/UI do, then return the repository."""
    return api_support.make_repo(store, settings)
