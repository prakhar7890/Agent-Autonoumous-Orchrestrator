"""Pydantic response models for the local read-mostly API.

Every field is transport-only: these models carry already-scrubbed strings produced by the
same CLI privacy helpers the rest of the API uses. They never echo credentials or env values.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Health(BaseModel):
    status: str = "ok"
    target: str  # "file <path>", "Aura", or "in-memory" — never credentials


class AgentRow(BaseModel):
    name: str
    version: str
    status: str
    input_to_output: str
    risk_tools: str
    sub_agents: str
    example_queries: str


class AgentDetail(BaseModel):
    name: str
    version: str
    status: str
    purpose: str
    usefulness: str
    input: str
    processing: str
    output: str
    model: str
    chainable: bool
    pipeline: list[str]
    tools: list[str]
    sub_agents: list[str]
    examples: list[str]


class VersionRow(BaseModel):
    version: str
    status: str
    author: str
    created_at: str
    default: str
    change_note: str


class LineageRow(BaseModel):
    version: str
    status: str
    chain: str


class VersionsResponse(BaseModel):
    agent: str
    default: str | None
    versions: list[VersionRow]
    lineage: list[LineageRow]


class TypedOutput(BaseModel):
    """Typed execution output. Never a bare string.

    - ``kind="text"`` carries ``text``.
    - ``kind="json"`` carries ``text`` (a JSON-encoded string).
    - ``kind="image"`` carries ``mime`` and ``data_base64`` (raw bytes base64-encoded); the
      client renders it itself and never receives an HTML-inlined copy. When the payload
      exceeds the configured cap the API degrades to ``kind="text"`` with an explanation.
    - ``model`` names the provider model that produced a real image (absent for stubs).
    """

    kind: str = Field(pattern="^(text|json|image)$")
    text: str | None = None
    mime: str | None = None
    data_base64: str | None = None
    model: str | None = None


class PlanStep(BaseModel):
    """One plan step, exactly as stored on the plan the CLI prints.

    Fields the plan did not record are ``null`` (the caller renders these as "not recorded");
    the API never substitutes an invented default.
    """

    order: int
    role: str | None = None
    agent: str | None = None
    version: str | None = None
    what_it_does: str | None = None
    why_chosen: str | None = None
    matched_terms: list[str] | None = None
    score: float | None = None
    how_it_helps: str | None = None
    input: str
    output: str
    tools: list[str]
    model: str | None = None


class PlanHeader(BaseModel):
    query_type: str | None = None
    payload: str
    goal: str | None = None
    intent_terms: list[str] | None = None
    dropped_modality_words: str | None = None
    reused_workflow: str | None = None
    alternatives: int


class AskResponse(BaseModel):
    status: str
    execution_id: str
    workflow_id: str
    break_reason: str
    user_message: str
    clarifying_question: str
    output: TypedOutput
    proposal_id: str
    agent_request_id: str
    steps_taken: int
    tokens_used: int
    elapsed_seconds: float
    upload_filename: str | None = None
    plan: PlanHeader | None = None
    steps: list[PlanStep] = Field(default_factory=list)


class ExecutionRow(BaseModel):
    execution: str
    status: str
    breakpoint: str
    query: str
    created_at: str


class ExecutionDetail(BaseModel):
    execution_id: str
    rendered: str
    output: TypedOutput


class EvaluationResult(BaseModel):
    baseline_accuracy: float | None = None
    candidate_accuracy: float | None = None
    regressions: list[str] = Field(default_factory=list)
    latency_ok: bool | None = None
    token_budget_ok: bool | None = None


class ProposalRow(BaseModel):
    proposal: str
    status: str
    agent: str
    base_version: str
    candidate_version: str
    trigger: str
    created_at: str
    evaluation: EvaluationResult


class ProposalsResponse(BaseModel):
    proposals: list[ProposalRow]
