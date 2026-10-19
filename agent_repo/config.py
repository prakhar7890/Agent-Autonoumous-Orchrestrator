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


@dataclass(frozen=True)
class ExecutionLimits:
    """Breakpoint limits (spec §6)."""
    max_retries_per_tool: int = 3
    max_steps: int = 8
    timeout_seconds: float = 30.0
    token_budget: int = 20_000
    max_alternative_workflows: int = 1


@dataclass(frozen=True)
class Settings:
    hash_salt: str = ""
    neo4j: Neo4jSettings | None = None
    router: RouterConfig = field(default_factory=RouterConfig)
    limits: ExecutionLimits = field(default_factory=ExecutionLimits)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, env_file: Path | None = None) -> Settings:
        """Build settings from `env` (defaults to os.environ) layered over `env_file` (defaults to ./.env)."""
        values: dict[str, str] = {}
        file_path = env_file if env_file is not None else Path(".env")
        if file_path.is_file():
            values.update({
                k: v for k, v in dotenv_values(file_path).items()
                if v is not None and k != "NEO4J_TEST_WIPE"
            })
        values.update(env if env is not None else os.environ)

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
            purpose_weight=float(values.get("AGENT_REPO_PURPOSE_WEIGHT", "1.0")),
            usefulness_weight=float(values.get("AGENT_REPO_USEFULNESS_WEIGHT", "0.7")),
            examples_weight=float(values.get("AGENT_REPO_EXAMPLES_WEIGHT", "1.3")),
            min_score_threshold=float(values.get("AGENT_REPO_MIN_SCORE_THRESHOLD", "0.12")),
        )
        limits = ExecutionLimits(
            max_retries_per_tool=int(values.get("AGENT_REPO_MAX_RETRIES", "3")),
            max_steps=int(values.get("AGENT_REPO_MAX_STEPS", "8")),
            timeout_seconds=float(values.get("AGENT_REPO_TIMEOUT_SECONDS", "30.0")),
            token_budget=int(values.get("AGENT_REPO_TOKEN_BUDGET", "20000")),
            max_alternative_workflows=int(values.get("AGENT_REPO_MAX_ALTERNATIVES", "1")),
        )
        return cls(
            hash_salt=values.get("AGENT_REPO_HASH_SALT", ""),
            neo4j=neo4j,
            router=router,
            limits=limits,
        )
