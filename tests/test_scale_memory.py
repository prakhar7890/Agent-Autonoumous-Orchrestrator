"""Memory-backend scale check: routing stays correct and fast as the agent count grows.

This test intentionally does NOT use the `graph`/`seeded` fixtures, so it runs on the
in-memory backend only (it is not parametrised over `neo4j` and adds no AuraDB traffic).
"""

from __future__ import annotations

import time

from agent_repo.graph.store import InMemoryGraph
from agent_repo.manifest import AgentManifest, Modality
from agent_repo.repository import AgentRepository
from agent_repo.routing import Router
from agent_repo.tools import builtin_registry
from tests.conftest import MAINTAINER

AGENT_COUNT = 200
TIME_BUDGET_SECONDS = 5.0


def _synth_manifest(name: str) -> AgentManifest:
    return AgentManifest(
        name=name,
        purpose=f"Handles {name}.",
        usefulness=f"Suited to {name}.",
        input_modalities=[Modality.TEXT],
        input_description="plain text",
        processing_description="deterministic routing fixture",
        system_prompt="Return the input unchanged.",
        output_type=Modality.TEXT,
        output_description="plain text",
        example_queries=[f"use {name}"],
    )


def test_routing_scales_to_200_synthetic_agents_within_budget() -> None:
    repo = AgentRepository(InMemoryGraph(), builtin_registry())
    for i in range(AGENT_COUNT):
        name = f"synth{i:04d}"
        repo.create_agent(_synth_manifest(name), MAINTAINER)
        repo.submit_for_approval(name, "0.1", MAINTAINER)
        repo.approve(name, "0.1", MAINTAINER)
    assert len(repo.list_agents()) == AGENT_COUNT

    router = Router(repo)
    samples = ["synth0007", "synth0100", "synth0199"]

    start = time.perf_counter()
    for query in samples:
        outcome = router.route(query)
        assert outcome.plan is not None and outcome.break_reason is None
        goal = outcome.plan.steps[-1]
        assert goal.role == "goal"
        assert goal.agent == query
    elapsed = time.perf_counter() - start

    print(f"\n[scale] {AGENT_COUNT} agents, {len(samples)} queries routed in {elapsed:.4f}s")
    assert elapsed < TIME_BUDGET_SECONDS, (
        f"routing {len(samples)} queries over {AGENT_COUNT} agents took {elapsed:.3f}s "
        f"(budget {TIME_BUDGET_SECONDS}s)"
    )
