"""Milestone 3 tests: LangGraph StateGraph engine, optional approval interrupt (`PendingApproval` +
`resume`), Mermaid diagram export, `LLMProvider` / `MockLLM` / `LangChainLLM` / `make_llm`, and the
equivalence test proving `PlainEngine` and `LangGraphEngine` produce identical results across
shared scenarios (success, chained prerequisites, approval decline, retries + alternative_skipped,
and breakpoints).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.chat_models import SimpleChatModel
from langchain_core.messages import BaseMessage

from agent_repo.config import ExecutionLimits
from agent_repo.execution.engines import LangGraphEngine, PendingApproval, PlainEngine
from agent_repo.execution.runner import ExecutionResult, Runner
from agent_repo.graph.store import InMemoryGraph
from agent_repo.llm import LangChainLLM, LLMError, MockLLM, make_llm
from agent_repo.manifest import AgentManifest, Modality, PipelineStep, RiskTier
from agent_repo.repository import AgentRepository
from agent_repo.routing import BreakReason
from agent_repo.seeds import seed_repository
from agent_repo.tools import AuthorityGrant, CallContext, Payload, ToolError, ToolSpec, builtin_registry
from tests.conftest import MAINTAINER


class _FakeLangChainChatModel(SimpleChatModel):
    """Deterministic fake LangChain `BaseChatModel` for offline testing of `LangChainLLM`."""

    reply_text: str = '{"proposed_purpose": "Fake chat model draft", "input_modalities": ["text"], "output_type": "text"}'

    @property
    def _llm_type(self) -> str:
        return "fake-chat-model"

    def _call(self, messages: list[BaseMessage], stop: list[str] | None = None, **kwargs: Any) -> str:
        return self.reply_text


# ---------------------------------------------------------------- 1. LLM PROVIDER & FACTORY TESTS

def test_mock_llm_and_make_llm_factory() -> None:
    llm = make_llm("mock:test-v1")
    assert isinstance(llm, MockLLM)
    assert llm.is_mock is True
    assert llm.provider_name == "mock" and llm.model_name == "test-v1"

    r1 = llm.generate("Improve summarizer")
    assert "MOCK LLM" in r1.text and r1.is_mock is True

    llm.queue_response("custom scripted reply")
    assert llm.generate("hello").text == "custom scripted reply"

    draft = llm.draft_agent("bake a cake", Modality.TEXT)
    assert draft["generator"] == "MOCK LLM" and draft["routable"] is False and draft["draft_only"] is True


def test_langchain_adapter_and_factory_errors(tmp_path: Path) -> None:
    fake_chat = _FakeLangChainChatModel()
    adapter = make_llm("openai:gpt-4o-mini", chat_model=fake_chat)
    assert isinstance(adapter, LangChainLLM)
    assert adapter.is_mock is False
    assert adapter.provider_name == "openai" and adapter.model_name == "gpt-4o-mini"

    resp = adapter.generate("Test prompt", system_prompt="System instruction")
    assert "Fake chat model draft" in resp.text

    draft = adapter.draft_agent("convert video to gif", Modality.FILE)
    assert draft["routable"] is False and draft["draft_only"] is True
    assert draft["generator"] == "openai:gpt-4o-mini"

    # Missing API keys or unknown providers fail clearly when no chat_model is injected
    for spec in ("gemini:gemini-1.5-flash", "groq:llama-3.1-8b-instant", "openai:gpt-4o-mini", "unknown:model"):
        with pytest.raises(LLMError):
            make_llm(spec, env={"GOOGLE_API_KEY": "", "GROQ_API_KEY": "", "OPENAI_API_KEY": ""})


# ---------------------------------------------------------------- 2. MERMAID & INTERRUPT TESTS

def test_langgraph_mermaid_diagram_has_all_nodes() -> None:
    repo = AgentRepository(InMemoryGraph(), builtin_registry())
    seed_repository(repo, MAINTAINER)
    engine = LangGraphEngine(Runner(repo), require_approval=True)
    mermaid = engine.mermaid_diagram()
    for node_name in ("plan", "approval", "attempt", "learn", "finish_broken", "finish_declined"):
        assert node_name in mermaid


@pytest.mark.neo4j_smoke
def test_langgraph_approval_interrupt_pause_and_resume(seeded: AgentRepository) -> None:
    runner = Runner(seeded)
    engine = LangGraphEngine(runner, require_approval=True)

    # 1) Start pauses before `approval` and returns PendingApproval with the full explainable Plan
    paused = engine.start("summarize the key points of this article: First point. Second point.")
    assert isinstance(paused, PendingApproval)
    assert paused.plan.goal_agent == "text_summarizer"
    # Nothing has executed or been written as an Execution node yet
    assert seeded.graph.find_nodes("Execution") == []

    # 2) Resume with approved=False -> exits via finish_declined
    declined = engine.resume(paused, approved=False)
    assert declined.status == "declined"
    assert declined.output is None

    # 3) Start again and resume with approved=True -> executes attempt -> learn and succeeds
    paused2 = engine.start("summarize the key points of this article: Alpha. Beta. Gamma.")
    assert isinstance(paused2, PendingApproval)
    approved_res = engine.resume(paused2, approved=True)
    assert approved_res.status == "succeeded"
    assert approved_res.output is not None and approved_res.output.text


# ---------------------------------------------------------------- 3. EQUIVALENCE TEST: PLAIN vs LANGGRAPH

def _normalize_result(res: ExecutionResult) -> dict[str, Any]:
    """Compare all semantic fields of `ExecutionResult` (stripping generated random UUIDs)."""
    return {
        "status": res.status,
        "output": res.output.model_dump(mode="json") if res.output else None,
        "goal_agent": res.plan.goal_agent if res.plan else None,
        "goal_version": res.plan.goal_version if res.plan else None,
        "plan_steps": [(s.role, s.agent, s.version, s.reason) for s in res.plan.steps] if res.plan else None,
        "reused_workflow": bool(res.plan and res.plan.reused_workflow_id),
        "break_reason": res.break_reason,
        "user_message": res.user_message,
        "clarifying_question": res.clarifying_question,
        "has_escalation": res.escalation_id is not None,
        "has_proposal": res.proposal_id is not None,
        "has_agent_request": res.agent_request_id is not None,
        "steps_taken": res.steps_taken,
        "tokens_used": res.tokens_used,
        "trace_events": [
            (
                e.event,
                {k: ("<wf_id>" if k in ("workflow_id", "reused_workflow_id") and v is not None else v)
                 for k, v in e.detail.items()},
            )
            for e in res.trace
        ],
    }


def _build_identical_env() -> tuple[Runner, dict[str, int]]:
    fixed_clock = lambda: datetime(2026, 10, 16, 12, 0, 0, tzinfo=UTC)  # noqa: E731
    reg = builtin_registry(clock=fixed_clock)
    counters = {"flaky_calls": 0}

    def flaky_tool(p: Payload, params: dict[str, object]) -> Payload:
        counters["flaky_calls"] += 1
        raise ToolError("service unavailable")

    reg.register(
        ToolSpec("flaky_tool", "always fails", RiskTier.LOW, (Modality.TEXT,), Modality.TEXT, True, flaky_tool)
    )
    repo = AgentRepository(InMemoryGraph(), reg, clock=fixed_clock)
    seed_repository(repo, MAINTAINER)

    for name in ("flaky_primary", "flaky_secondary"):
        m = AgentManifest(
            name=name,
            purpose="Proofread legal contracts for typos.",
            usefulness="Finds typos in legal contracts.",
            input_modalities=[Modality.TEXT],
            input_description="contract text",
            processing_description="calls flaky_tool",
            pipeline=[PipelineStep(kind="tool", name="flaky_tool")],
            output_type=Modality.TEXT,
            output_description="proofread text",
            example_queries=["proofread this legal contract for typos"],
        )
        rec = repo.create_agent(m, author=MAINTAINER)
        repo.submit_for_approval(rec.agent, rec.version, MAINTAINER)
        repo.approve(rec.agent, rec.version, MAINTAINER)

    runner = Runner(
        repo,
        limits=ExecutionLimits(max_retries_per_tool=2, max_alternative_workflows=1),
        clock=fixed_clock,
        timer=lambda: 100.0,
    )
    return runner, counters


def test_plain_engine_and_langgraph_engine_give_identical_results(tmp_path: Path) -> None:
    """Proves `LangGraphEngine` and `PlainEngine` produce identical results across the exact
    same sequence of scenarios by sharing the node functions and retry/limit/breakpoint policy.
    """
    audio_file = tmp_path / "note.wav"
    audio_file.write_bytes(b"RIFF....")
    (tmp_path / "note.transcript.txt").write_text(
        "The team reviewed milestone three. LangGraph and PlainEngine matched on every scenario.",
        encoding="utf-8",
    )

    runner_plain, counts_plain = _build_identical_env()
    runner_lg, counts_lg = _build_identical_env()

    plain = PlainEngine(runner_plain)
    lg = LangGraphEngine(runner_lg, require_approval=True)

    scenarios: list[dict[str, Any]] = [
        # 1. Direct text summarization
        {"query": "summarize this text: One. Two. Three."},
        # 2. Chained prerequisite (audio -> audio_transcriber -> text_summarizer)
        {"query": "summarize this audio recording", "attachment_path": str(audio_file)},
        # 3. Workflow memory reuse on second audio summary query
        {"query": "give me a short summary of this audio", "attachment_path": str(audio_file)},
        # 4. Plan declined at approval interrupt
        {"query": "summarize this text: Declined.", "approve_plan": lambda p: False},
        # 5. HIGH-risk tool denied without grant
        {"query": "send an email to the team"},
        # 6. HIGH-risk tool allowed with human grant
        {
            "query": "send an email to the team",
            "ctx": CallContext(actor="bob", grants=(AuthorityGrant("email_send", "carol", "approved"),)),
        },
        # 7. No-match breakpoint + draft AgentRequest
        {"query": "bake a sourdough pizza at 500 degrees"},
        # 8. Missing-input breakpoint (no image attached)
        {"query": "classify this picture and label what is in the photo"},
        # 9. Max retries + alternative_skipped (shared retry policy)
        {"query": "proofread this legal contract for typos"},
    ]

    for idx, sc in enumerate(scenarios):
        r_p = plain.run(**sc)
        r_l = lg.run(**sc)
        assert _normalize_result(r_p) == _normalize_result(r_l), f"Mismatch on scenario #{idx + 1}: {sc['query']}"

    # Both engines called the failing tool the exact same number of times (1 initial + 2 retries = 3,
    # and skipped the secondary alternative because it uses the same exhausted tool).
    assert counts_plain["flaky_calls"] == counts_lg["flaky_calls"] == 3

