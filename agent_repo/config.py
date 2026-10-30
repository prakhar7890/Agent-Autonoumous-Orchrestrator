"""Runtime settings loaded from environment variables and an optional `.env` file.

Only non-secret defaults live in code. Secrets (salt, passwords, API keys) come from the
environment. Settings are an explicit object passed around; there is no module-level state.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values


def merged_env(env: Mapping[str, str] | None = None, env_file: Path | None = None) -> dict[str, str]:
    """Return `env` (defaults to `os.environ`) layered over the `.env` file.

    `NEO4J_TEST_WIPE` is never read from `.env` (it may only come from the shell). Secrets are
    returned only for in-process use; callers must never print or log the result.
    """
    values: dict[str, str] = {}
    file_path = env_file if env_file is not None else Path(".env")
    if file_path.is_file():
        values.update({
            k: v for k, v in dotenv_values(file_path).items()
            if v is not None and k != "NEO4J_TEST_WIPE"
        })
    values.update(env if env is not None else os.environ)
    return values


@dataclass(frozen=True)
class Neo4jSettings:
    uri: str
    user: str
    password: str
    database: str


@dataclass(frozen=True)
class RouterConfig:
    """Scoring weights and threshold for routing from facts (never hardcoded in logic)."""
    purpose_weight: float = 1.0
    usefulness_weight: float = 0.7
    examples_weight: float = 1.3
    min_score_threshold: float = 0.12
    # Near-top score window within which payload-compatible candidates and alternative workflows
    # are considered. Default 0.15 was originally set in Milestone 2 for alternative-workflow
    # collection and verified against Set 1 failures (where compatible candidates on rows 1, 3, 4,
    # 5, and 10 scored within 0.0042-0.0538 of the top candidate) when used for payload tie-breaking.
    tie_break_margin: float = 0.15


@dataclass(frozen=True)
class ExecutionLimits:
    """Breakpoint limits (spec §6)."""
    max_retries_per_tool: int = 3
    max_steps: int = 8
    timeout_seconds: float = 30.0
    token_budget: int = 20_000
    max_alternative_workflows: int = 1


_ALLOWED_TRANSCRIBERS = ("stub", "groq")
_ALLOWED_IMAGE_CLASSIFIERS = ("stub", "gemini")
_ALLOWED_IMAGE_GENERATORS = ("stub", "gemini", "huggingface", "hf")

# Verified available via live `client.models.list()` on 2026-10-21 (this account's Google API).
# Google doc: https://ai.google.dev/gemini-api/docs/image-generation  (Imagen / nano-banana family)
_DEFAULT_GEMINI_IMAGE_MODEL = "gemini-nano-banana-2.1"
_DEFAULT_HF_IMAGE_MODEL = "black-forest-labs/FLUX.1-schnell"
_DEFAULT_MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 4 MB


@dataclass(frozen=True)
class ToolSettings:
    """Opt-in real tool providers (spec §2). Stubs stay the default so the offline suite and demo
    remain deterministic; real providers are selected per tool from the environment."""
    transcriber: str = "stub"              # stub | groq                    (AGENT_REPO_TRANSCRIBER)
    image_classifier: str = "stub"         # stub | gemini                  (AGENT_REPO_IMAGE_CLASSIFIER)
    image_generator: str = "stub"          # stub | gemini | huggingface    (AGENT_REPO_IMAGE_GENERATOR)
    image_generator_model: str = _DEFAULT_GEMINI_IMAGE_MODEL  # AGENT_REPO_GEMINI_IMAGE_MODEL
    hf_image_model: str = _DEFAULT_HF_IMAGE_MODEL             # AGENT_REPO_HF_IMAGE_MODEL
    max_image_bytes: int = _DEFAULT_MAX_IMAGE_BYTES           # AGENT_REPO_API_MAX_IMAGE_BYTES
    artifact_dir: str = ""                                    # AGENT_REPO_ARTIFACT_DIR

    def __post_init__(self) -> None:
        if self.transcriber not in _ALLOWED_TRANSCRIBERS:
            raise ValueError("AGENT_REPO_TRANSCRIBER must be 'stub' or 'groq'.")
        if self.image_classifier not in _ALLOWED_IMAGE_CLASSIFIERS:
            raise ValueError("AGENT_REPO_IMAGE_CLASSIFIER must be 'stub' or 'gemini'.")
        if self.image_generator not in _ALLOWED_IMAGE_GENERATORS:
            raise ValueError("AGENT_REPO_IMAGE_GENERATOR must be 'stub', 'gemini', or 'huggingface'.")
        if self.max_image_bytes <= 0:
            raise ValueError("AGENT_REPO_API_MAX_IMAGE_BYTES must be a positive integer.")


def _parse_float(values: Mapping[str, str], key: str, default: str) -> float:
    try:
        return float(values.get(key, default))
    except ValueError:
        raise ValueError(f"{key} must be a valid float.") from None


def _parse_int(values: Mapping[str, str], key: str, default: str) -> int:
    try:
        return int(values.get(key, default))
    except ValueError:
        raise ValueError(f"{key} must be a valid integer.") from None


@dataclass(frozen=True)
class Settings:
    hash_salt: str = ""
    neo4j: Neo4jSettings | None = None
    router: RouterConfig = field(default_factory=RouterConfig)
    limits: ExecutionLimits = field(default_factory=ExecutionLimits)
    tools: ToolSettings = field(default_factory=ToolSettings)

    def __post_init__(self) -> None:
        if self.hash_salt.strip().lower().startswith("change-me"):
            raise ValueError(
                "AGENT_REPO_HASH_SALT is still the placeholder from .env.example; generate a real "
                "secret with: python -c \"import secrets; print(secrets.token_hex(16))\""
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, env_file: Path | None = None) -> Settings:
        """Build settings from `env` (defaults to os.environ) layered over `env_file` (defaults to ./.env)."""
        values = merged_env(env, env_file)

        neo4j = None
        if values.get("NEO4J_URI"):
            user = values.get("NEO4J_USER") or values.get("NEO4J_USERNAME") or "neo4j"
            neo4j = Neo4jSettings(
                uri=values["NEO4J_URI"],
                user=user,
                password=values.get("NEO4J_PASSWORD", ""),
                database=values.get("NEO4J_DATABASE") or "neo4j",
            )
        router = RouterConfig(
            purpose_weight=_parse_float(values, "AGENT_REPO_PURPOSE_WEIGHT", "1.0"),
            usefulness_weight=_parse_float(values, "AGENT_REPO_USEFULNESS_WEIGHT", "0.7"),
            examples_weight=_parse_float(values, "AGENT_REPO_EXAMPLES_WEIGHT", "1.3"),
            min_score_threshold=_parse_float(values, "AGENT_REPO_MIN_SCORE_THRESHOLD", "0.12"),
            tie_break_margin=_parse_float(values, "AGENT_REPO_TIE_BREAK_MARGIN", "0.15"),
        )
        limits = ExecutionLimits(
            max_retries_per_tool=_parse_int(values, "AGENT_REPO_MAX_RETRIES", "3"),
            max_steps=_parse_int(values, "AGENT_REPO_MAX_STEPS", "8"),
            timeout_seconds=_parse_float(values, "AGENT_REPO_TIMEOUT_SECONDS", "30.0"),
            token_budget=_parse_int(values, "AGENT_REPO_TOKEN_BUDGET", "20000"),
            max_alternative_workflows=_parse_int(values, "AGENT_REPO_MAX_ALTERNATIVES", "1"),
        )
        tools = ToolSettings(
            transcriber=values.get("AGENT_REPO_TRANSCRIBER", "stub").strip().lower(),
            image_classifier=values.get("AGENT_REPO_IMAGE_CLASSIFIER", "stub").strip().lower(),
            image_generator=values.get("AGENT_REPO_IMAGE_GENERATOR", "stub").strip().lower(),
            image_generator_model=values.get("AGENT_REPO_GEMINI_IMAGE_MODEL", _DEFAULT_GEMINI_IMAGE_MODEL).strip(),
            hf_image_model=values.get("AGENT_REPO_HF_IMAGE_MODEL", _DEFAULT_HF_IMAGE_MODEL).strip() or _DEFAULT_HF_IMAGE_MODEL,
            max_image_bytes=_parse_int(values, "AGENT_REPO_API_MAX_IMAGE_BYTES", str(_DEFAULT_MAX_IMAGE_BYTES)),
            artifact_dir=values.get("AGENT_REPO_ARTIFACT_DIR", "").strip(),
        )
        return cls(
            hash_salt=values.get("AGENT_REPO_HASH_SALT", ""),
            neo4j=neo4j,
            router=router,
            limits=limits,
            tools=tools,
        )
