"""Agent manifest schema, validation and content hashing.

A manifest is the full, immutable definition of one agent version (spec §3).
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class Modality(StrEnum):
    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    FILE = "file"
    JSON = "json"


class RiskTier(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Status(StrEnum):
    CANDIDATE = "candidate"
    PENDING_APPROVAL = "pending_approval"
    STABLE = "stable"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"


class PipelineStep(BaseModel):
    """One ordered step: a tool call or a call to a (version-pinned) sub-agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["tool", "agent"]
    name: str
    version: str | None = None  # required for kind="agent": sub-agents are pinned, so versions are reproducible
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> PipelineStep:
        if not _NAME_RE.match(self.name):
            raise ValueError(f"invalid step name {self.name!r}")
        if self.kind == "agent" and not self.version:
            raise ValueError(f"sub-agent step {self.name!r} must pin a version")
        if self.kind == "tool" and self.version is not None:
            raise ValueError("tool steps do not take a version")
        return self


class AgentManifest(BaseModel):
    """Everything an agent version is. Frozen: any change must become a new version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    purpose: str
    usefulness: str
    input_modalities: list[Modality]
    input_description: str
    processing_description: str
    pipeline: list[PipelineStep] = Field(default_factory=list)
    system_prompt: str = ""
    output_type: Modality
    output_description: str
    output_schema: dict[str, Any] | None = None
    example_queries: list[str] = Field(default_factory=list)
    chainable: bool = False
    provider: str = "mock"
    model: str = "mock-1"

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError("name must be snake_case, 2-64 chars")
        return v

    @field_validator("purpose", "usefulness")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v.strip()

    @field_validator("input_modalities")
    @classmethod
    def _modalities(cls, v: list[Modality]) -> list[Modality]:
        if not v:
            raise ValueError("at least one input modality is required")
        if len(set(v)) != len(v):
            raise ValueError("duplicate input modalities")
        return v

    @model_validator(mode="after")
    def _pipeline_or_prompt(self) -> AgentManifest:
        if not self.pipeline and not self.system_prompt.strip():
            raise ValueError("a manifest needs a pipeline or a system prompt")
        return self

    def validate_output_data(self, data: Any) -> tuple[bool, str]:
        """Validate the optional pinned output schema without executing model code."""
        schema = self.output_schema
        if schema is None:
            return True, ""
        if not isinstance(schema, dict):
            return False, "output schema is not an object"
        expected_type = schema.get("type")
        if expected_type == "object" and not isinstance(data, dict):
            return False, "output data must be an object"
        if expected_type == "array" and not isinstance(data, list):
            return False, "output data must be an array"
        required = schema.get("required", [])
        if isinstance(required, list) and isinstance(data, dict):
            missing = [str(k) for k in required if k not in data]
            if missing:
                return False, f"output data is missing required field(s): {', '.join(missing)}"
        return True, ""

    def tool_names(self) -> list[str]:
        return [s.name for s in self.pipeline if s.kind == "tool"]

    def sub_agents(self) -> list[tuple[str, str]]:
        return [(s.name, s.version or "") for s in self.pipeline if s.kind == "agent"]

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_json(cls, raw: str) -> AgentManifest:
        return cls.model_validate_json(raw)

