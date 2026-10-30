"""Small, offline-safe domain contracts used at service boundaries.

The existing manifest, repository, routing, and execution models remain the runtime
implementation. These contracts provide stable serialized envelopes for the pieces that
cross those boundaries without introducing provider or database dependencies.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ImmutableContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class Agent(ImmutableContract):
    id: str
    name: str
    purpose: str
    capabilities: tuple[str, ...] = ()


class AgentVersion(ImmutableContract):
    id: str
    agent_id: str
    parent_id: str | None = None
    manifest: dict[str, Any]
    checksum: str
    status: Literal["candidate", "pending_approval", "stable", "deprecated", "rejected"] = "candidate"


class PromptVersion(ImmutableContract):
    id: str
    agent_version_id: str
    template: str
    checksum: str


class ToolVersion(ImmutableContract):
    id: str
    name: str
    version: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)


class ModelConfigVersion(ImmutableContract):
    id: str
    provider: str
    model: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ArtifactReference(ImmutableContract):
    id: str
    media_type: str
    checksum: str
    size_bytes: int = Field(ge=0)
    owner_scope: str


class StepDefinition(ImmutableContract):
    id: str
    kind: Literal["agent", "tool"]
    target_id: str
    input_bindings: dict[str, str] = Field(default_factory=dict)
    depends_on: tuple[str, ...] = ()


class WorkflowVersion(ImmutableContract):
    id: str
    steps: tuple[StepDefinition, ...]
    checksum: str


class ExecutionPlan(ImmutableContract):
    id: str
    workflow_version_id: str
    steps: tuple[StepDefinition, ...]
    token_budget: int = Field(gt=0)
    timeout_seconds: float = Field(gt=0)


class Execution(ImmutableContract):
    id: str
    plan_id: str
    status: Literal["running", "awaiting_input", "awaiting_approval", "succeeded", "failed", "cancelled", "budget_exceeded"]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class StepRun(ImmutableContract):
    id: str
    execution_id: str
    step_id: str
    agent_version_id: str | None = None
    attempt: int = Field(ge=1)
    status: str
    usage: dict[str, Any] = Field(default_factory=dict)


class Feedback(ImmutableContract):
    id: str
    execution_id: str
    category: Literal["supported", "preference", "contradictory", "unverifiable", "suspected_abuse"]
    text: str


class ChangeProposal(ImmutableContract):
    id: str
    parent_version_id: str
    candidate_checksum: str
    evidence: tuple[str, ...] = ()
    status: Literal["open", "pending_approval", "approved", "rejected"] = "open"


class Approval(ImmutableContract):
    id: str
    proposal_id: str
    candidate_checksum: str
    approved_by: str
    approved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
