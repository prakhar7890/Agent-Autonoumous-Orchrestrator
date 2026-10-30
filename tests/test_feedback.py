from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from agent_repo.actors import NotHumanError
from agent_repo.config import ExecutionLimits
from agent_repo.execution.runner import Runner
from agent_repo.feedback import (
    AutomaticCase,
    CaseCheck,
    FeedbackError,
    FeedbackLoop,
    ensure_open_proposal,
)
from agent_repo.llm import MockLLM
from agent_repo.manifest import AgentManifest, Modality, PipelineStep, RiskTier, Status
from agent_repo.privacy import UserIdHasher
from agent_repo.repository import AgentRepository
from agent_repo.routing import Router
from agent_repo.tools import Payload, ToolError, ToolSpec


SALT = "feedback-test-salt-at-least-16"
SAMPLE_TEXT = "Alpha milestone. Beta launch."
REWRITE_TO_ONE_SENTENCE = json.dumps({
    "system_prompt": "Summarize faithfully and omit irrelevant detail.",
    "pipeline": [{"kind": "tool", "name": "text_summarize", "params": {"max_sentences": 1}}],
})


@pytest.fixture
def feedback_context(seeded: AgentRepository):
    execution = Runner(seeded).execute_query(f"summarize the key points of this report: {SAMPLE_TEXT}")
    assert execution.status == "succeeded"
    assert execution.workflow_id is not None
    loop = FeedbackLoop(seeded, UserIdHasher(SALT), MockLLM())
    return seeded, execution, loop


def _case(
    name: str,
    *checks: CaseCheck,
    text: str = SAMPLE_TEXT,
    latency_budget_seconds: float | None = None,
    token_budget: int | None = None,
) -> AutomaticCase:
    return AutomaticCase(
        name=name,
        payload=Payload(modality=Modality.TEXT, text=text),
        checks=list(checks),
        latency_budget_seconds=latency_budget_seconds,
        token_budget=token_budget,
    )


def _proposal(repo: AgentRepository, loop: FeedbackLoop, workflow_id: str) -> str:
    workflow = repo.graph.get_node(workflow_id)
    assert workflow is not None
    return ensure_open_proposal(
        repo,
        workflow_id,
        str(workflow.props["goal_agent"]),
        str(workflow.props["goal_version"]),
        "test",
        datetime.now(UTC).isoformat(),
    )


def _queue_rewrite(loop: FeedbackLoop, raw: str = REWRITE_TO_ONE_SENTENCE) -> None:
    assert isinstance(loop.llm, MockLLM)
    loop.llm.queue_response(raw)


def test_feedback_requires_identified_user_valid_rating_and_negative_comment(feedback_context) -> None:
    _, execution, loop = feedback_context
    for user_id in ("", "   "):
        with pytest.raises(FeedbackError, match="identified user"):
            loop.record_feedback(execution.execution_id, user_id, 1)
    for rating in (0, 2, True, -1.0):
        with pytest.raises(FeedbackError, match="rating"):
            loop.record_feedback(execution.execution_id, "alice", rating)  # type: ignore[arg-type]
    for comment in (None, "", "n/a", "bad"):
        with pytest.raises(FeedbackError, match="substantive"):
            loop.record_feedback(execution.execution_id, "alice", -1, comment)
    with pytest.raises(FeedbackError, match="comment must be text"):
        loop.record_feedback(execution.execution_id, "alice", 1, 123)  # type: ignore[arg-type]
    assert loop.record_feedback(execution.execution_id, "alice", 1).feedback_id


def test_feedback_is_hashed_scrubbed_and_one_per_user_per_execution(feedback_context) -> None:
    repo, execution, loop = feedback_context
    submitted = loop.record_feedback(
        execution.execution_id,
        "alice@example.com",
        -1,
        "This summary is wrong; contact alice@example.com.",
    )
    feedback = repo.graph.get_node(submitted.feedback_id)
    assert feedback is not None
    assert feedback.props["user_hash"] == UserIdHasher(SALT).hash("alice@example.com")
    assert feedback.props["user_hash"] != "alice@example.com"
    assert "user_id" not in feedback.props
    assert "alice@example.com" not in feedback.props["comment"]
    assert "[REDACTED_EMAIL]" in feedback.props["comment"]
    with pytest.raises(FeedbackError, match="already rated"):
        loop.record_feedback(execution.execution_id, "alice@example.com", 1)


def test_negative_feedback_from_distinct_users_opens_one_proposal(feedback_context) -> None:
    repo, execution, loop = feedback_context
    results = [
        loop.record_feedback(execution.execution_id, f"user-{i}", -1, "The summary omitted facts.")
        for i in range(1, 4)
    ]
    assert [result.proposal_id for result in results[:2]] == [None, None]
    assert results[2].proposal_id is not None
    assert len(repo.graph.find_nodes("Proposal")) == 1
    proposal = repo.graph.get_node(results[2].proposal_id)
    assert proposal is not None and proposal.props["trigger"] == "negative_feedback"
    assert proposal.props["status"] == "open"


def test_hourly_feedback_limit_is_enforced_per_hashed_user(seeded: AgentRepository) -> None:
    clock = [datetime(2026, 10, 16, 12, 0, tzinfo=UTC)]
    loop = FeedbackLoop(
        seeded,
        UserIdHasher(SALT),
        MockLLM(),
        hourly_limit=2,
        clock=lambda: clock[0],
    )
    runner = Runner(seeded)
    first = runner.execute_query("summarize the key points of this report: Alpha. Beta.")
    second = runner.execute_query("summarize this report: Delta. Epsilon.")
    assert first.status == second.status == "succeeded"
    loop.record_feedback(first.execution_id, "person-1", 1)
    clock[0] += timedelta(minutes=30)
    loop.record_feedback(second.execution_id, "person-1", 1)
    third = runner.execute_query("summarize this report: Gamma. Omega.")
    assert third.status == "succeeded"
    with pytest.raises(FeedbackError, match="hourly"):
        loop.record_feedback(third.execution_id, "person-1", 1)
    clock[0] += timedelta(hours=1)
    assert loop.record_feedback(third.execution_id, "person-1", 1).feedback_id


def test_cases_support_exact_fields_and_must_not_contain(feedback_context) -> None:
    repo, execution, loop = feedback_context
    case_id = loop.add_case(execution.workflow_id, _case(
        "exact output",
        CaseCheck(kind="exact_field", field="modality", expected="text"),
        CaseCheck(kind="exact_field", field="text", expected=SAMPLE_TEXT),
        CaseCheck(kind="must_not_contain", value="private marker"),
    ))
    result, = loop.evaluate_cases(execution.workflow_id)
    assert result.case_id == case_id and result.passed
    stored = repo.graph.get_node(case_id)
    assert stored is not None and stored.props["last_passed"] is True


def test_failing_automatic_case_triggers_one_proposal(feedback_context) -> None:
    repo, execution, loop = feedback_context
    loop.add_case(execution.workflow_id, _case(
        "reject unwanted sentence",
        CaseCheck(kind="must_not_contain", value="Beta launch"),
    ))
    result, = loop.evaluate_cases(execution.workflow_id)
    assert not result.passed
    proposals = repo.graph.find_nodes("Proposal", {"workflow_id": execution.workflow_id})
    assert len(proposals) == 1
    assert proposals[0].props["trigger"] == "automatic_case_failure"
    same = _proposal(repo, loop, execution.workflow_id)
    assert same == proposals[0].id


def test_candidate_rewrite_is_labelled_mock_and_changes_only_allowed_fields(feedback_context) -> None:
    repo, execution, loop = feedback_context
    proposal_id = _proposal(repo, loop, execution.workflow_id)
    _queue_rewrite(loop)
    version = loop.generate_candidate(proposal_id)
    candidate = repo.get_version("text_summarizer", version)
    base = repo.get_version("text_summarizer", "0.1")
    assert candidate.author == "llm:mock"
    assert candidate.status is Status.CANDIDATE
    candidate_manifest = candidate.manifest.model_dump(mode="json")
    base_manifest = base.manifest.model_dump(mode="json")
    assert candidate_manifest == {
        **base_manifest,
        "system_prompt": candidate.manifest.system_prompt,
        "pipeline": [step.model_dump(mode="json") for step in candidate.manifest.pipeline],
    }
    assert candidate.manifest.input_modalities == base.manifest.input_modalities
    assert candidate.manifest.output_type == base.manifest.output_type
    assert candidate.manifest.system_prompt != base.manifest.system_prompt
    assert candidate.manifest.pipeline != base.manifest.pipeline
    proposal = repo.graph.get_node(proposal_id)
    assert proposal is not None and proposal.props["rewrite_label"] == "MOCK LLM: scripted rewrite"


def test_candidate_version_is_linked_from_proposal_by_proposes_edge(feedback_context) -> None:
    repo, execution, loop = feedback_context
    proposal_id = _proposal(repo, loop, execution.workflow_id)
    assert repo.graph.rels(start=proposal_id, rel_type="PROPOSES") == []
    _queue_rewrite(loop)
    version = loop.generate_candidate(proposal_id)
    candidate = repo.get_version("text_summarizer", version)
    proposals = repo.graph.rels(start=proposal_id, rel_type="PROPOSES")
    assert [rel.end for rel in proposals] == [candidate.node_id]
    assert proposals[0].type == "PROPOSES"


@pytest.mark.parametrize("fence", ["```json", "```"])
def test_fenced_json_rewrite_is_accepted_and_prompt_includes_context(
    feedback_context, fence: str
) -> None:
    repo, execution, loop = feedback_context
    proposal_id = _proposal(repo, loop, execution.workflow_id)
    fenced_payload = f"{fence}\n{REWRITE_TO_ONE_SENTENCE}\n```"
    _queue_rewrite(loop, fenced_payload)
    version = loop.generate_candidate(proposal_id)
    candidate = repo.get_version("text_summarizer", version)
    assert candidate.status is Status.CANDIDATE
    assert repo.graph.get_node(proposal_id).props.get("rewrite_error") == ""
    assert isinstance(loop.llm, MockLLM)
    _sent_sys, sent_prompt = loop.llm.calls[-1]
    assert "Current pipeline:" in sent_prompt
    assert "Allowed tool names:" in sent_prompt
    assert "text_summarize" in sent_prompt
    assert "email_send" not in sent_prompt and "email_sender" not in sent_prompt
    assert "Allowed agent names:" in sent_prompt
    assert "audio_transcriber@0.1" in sent_prompt


@pytest.mark.parametrize("raw,expected_reason", [
    ("not json", "invalid JSON"),
    ("```json\nnot valid json at all\n```", "invalid JSON"),
    (
        json.dumps({"system_prompt": "rewrite", "pipeline": [], "extra_key": True}),
        "rewrite JSON must contain exactly system_prompt and pipeline",
    ),
    (
        "```json\n" + json.dumps({"system_prompt": "rewrite", "pipeline": [], "extra_key": True}) + "\n```",
        "rewrite JSON must contain exactly system_prompt and pipeline",
    ),
    (
        json.dumps({"system_prompt": "rewrite", "pipeline": [], "input_modalities": ["audio"]}),
        "rewrite JSON must contain exactly system_prompt and pipeline",
    ),
    (
        json.dumps({"system_prompt": "rewrite", "pipeline": [], "output_type": "json"}),
        "rewrite JSON must contain exactly system_prompt and pipeline",
    ),
    (
        json.dumps({"system_prompt": "rewrite", "pipeline": "text_summarize"}),
        "rewrite system_prompt must be a string and pipeline must be an array",
    ),
])
def test_invalid_rewrite_shapes_fenced_garbage_extra_keys_and_modality_change_fail_safely(
    feedback_context, raw: str, expected_reason: str
) -> None:
    repo, execution, loop = feedback_context
    proposal_id = _proposal(repo, loop, execution.workflow_id)
    _queue_rewrite(loop, raw)
    versions_before = len(repo.versions("text_summarizer"))
    with pytest.raises(FeedbackError):
        loop.generate_candidate(proposal_id)
    assert len(repo.versions("text_summarizer")) == versions_before
    err = repo.graph.get_node(proposal_id).props.get("rewrite_error")
    assert err == expected_reason
    assert err != "RepositoryError"


@pytest.mark.parametrize("pipeline,expected_substr", [
    ([{"kind": "tool", "name": "rm_rf"}], "unknown tool 'rm_rf'"),
    ([{"kind": "tool", "name": "email_send"}], "non-human authors cannot add HIGH-risk tool 'email_send'"),
    (
        [{"kind": "agent", "name": "email_sender", "version": "0.1"}],
        "non-human authors cannot add sub-agent email_sender@0.1: it reaches a HIGH-risk tool",
    ),
])
def test_rewrite_cannot_add_unknown_or_high_risk_tool_directly_or_transitively(
    feedback_context, pipeline: list[dict[str, object]], expected_substr: str
) -> None:
    repo, execution, loop = feedback_context
    proposal_id = _proposal(repo, loop, execution.workflow_id)
    _queue_rewrite(loop, json.dumps({"system_prompt": "untrusted", "pipeline": pipeline}))
    versions_before = len(repo.versions("text_summarizer"))
    with pytest.raises(FeedbackError, match="rejected"):
        loop.generate_candidate(proposal_id)
    assert len(repo.versions("text_summarizer")) == versions_before
    err = str(repo.graph.get_node(proposal_id).props.get("rewrite_error", ""))
    assert expected_substr in err
    assert err not in {"RepositoryError", "GuardrailError"}


def test_eval_gate_requires_strict_accuracy_improvement_without_regression(feedback_context) -> None:
    repo, execution, loop = feedback_context
    workflow_id = execution.workflow_id
    loop.add_case(workflow_id, _case(
        "must omit second sentence",
        CaseCheck(kind="must_not_contain", value="Beta launch"),
    ))
    loop.add_case(workflow_id, _case(
        "previously passing modality check",
        CaseCheck(kind="exact_field", field="modality", expected="text"),
    ))
    proposal_id = _proposal(repo, loop, workflow_id)
    _queue_rewrite(loop)
    loop.generate_candidate(proposal_id)
    evaluation = loop.evaluate_proposal(proposal_id)
    assert evaluation.status == "pending_approval"
    assert evaluation.baseline_accuracy == 0.5
    assert evaluation.candidate_accuracy == 1.0
    assert not evaluation.regressions
    assert evaluation.latency_ok and evaluation.token_budget_ok


def test_eval_gate_rejects_useless_mock_rewrite(feedback_context) -> None:
    repo, execution, loop = feedback_context
    workflow_id = execution.workflow_id
    loop.add_case(workflow_id, _case(
        "exact text remains unchanged",
        CaseCheck(kind="exact_field", field="text", expected=SAMPLE_TEXT),
    ))
    proposal_id = _proposal(repo, loop, workflow_id)
    _queue_rewrite(loop, json.dumps({
        "system_prompt": "Use different words, but keep behavior unchanged.",
        "pipeline": [{"kind": "tool", "name": "text_summarize", "params": {"max_sentences": 2}}],
    }))
    loop.generate_candidate(proposal_id)
    evaluation = loop.evaluate_proposal(proposal_id)
    assert evaluation.status == "rejected_by_gate"
    assert evaluation.baseline_accuracy == evaluation.candidate_accuracy == 1.0


def test_eval_gate_rejects_regression_even_when_accuracy_improves(feedback_context) -> None:
    repo, execution, loop = feedback_context
    workflow_id = execution.workflow_id
    loop.add_case(workflow_id, _case(
        "full two sentence output",
        CaseCheck(kind="exact_field", field="text", expected=SAMPLE_TEXT),
    ))
    loop.add_case(workflow_id, _case(
        "omit beta sentence",
        CaseCheck(kind="must_not_contain", value="Beta launch"),
    ))
    loop.add_case(workflow_id, _case(
        "omit middle sentence",
        CaseCheck(kind="must_not_contain", value="Gamma middle"),
        text="Alpha start. Gamma middle. Omega final.",
    ))
    proposal_id = _proposal(repo, loop, workflow_id)
    _queue_rewrite(loop)
    loop.generate_candidate(proposal_id)
    evaluation = loop.evaluate_proposal(proposal_id)
    assert evaluation.baseline_accuracy == pytest.approx(1 / 3)
    assert evaluation.candidate_accuracy == pytest.approx(2 / 3)
    assert len(evaluation.regressions) == 1
    assert evaluation.status == "rejected_by_gate"


@pytest.mark.parametrize("budget", ["latency", "tokens"])
def test_eval_gate_enforces_latency_and_token_budgets(feedback_context, budget: str) -> None:
    repo, execution, loop = feedback_context
    check = CaseCheck(kind="must_not_contain", value="Beta launch")
    case = _case(
        f"{budget} budget",
        check,
        latency_budget_seconds=1e-12 if budget == "latency" else None,
        token_budget=0 if budget == "tokens" else None,
    )
    loop.add_case(execution.workflow_id, case)
    proposal_id = _proposal(repo, loop, execution.workflow_id)
    _queue_rewrite(loop)
    loop.generate_candidate(proposal_id)
    evaluation = loop.evaluate_proposal(proposal_id)
    assert evaluation.candidate_accuracy > evaluation.baseline_accuracy
    if budget == "latency":
        assert not evaluation.latency_ok
    else:
        assert not evaluation.token_budget_ok
    assert evaluation.status == "rejected_by_gate"


def test_no_cases_means_needs_cases_and_never_approves(feedback_context) -> None:
    repo, execution, loop = feedback_context
    proposal_id = _proposal(repo, loop, execution.workflow_id)
    _queue_rewrite(loop)
    loop.generate_candidate(proposal_id)
    result = loop.evaluate_proposal(proposal_id)
    assert result.status == "needs_cases"
    assert repo.graph.get_node(proposal_id).props["status"] == "needs_cases"
    with pytest.raises(FeedbackError, match="passed the evaluation gate"):
        loop.approve_proposal(proposal_id, "alice")
    assert repo.default_version("text_summarizer").version == "0.1"


@pytest.mark.parametrize("machine_approver", ["llm:reviewer", "system:reviewer"])
def test_human_approval_repoints_query_workflow_without_changing_agent_default(
    feedback_context, machine_approver: str
) -> None:
    repo, execution, loop = feedback_context
    workflow_id = execution.workflow_id
    loop.add_case(workflow_id, _case(
        "omit second sentence",
        CaseCheck(kind="must_not_contain", value="Beta launch"),
    ))
    loop.add_case(workflow_id, _case(
        "modality invariant",
        CaseCheck(kind="exact_field", field="modality", expected="text"),
    ))
    proposal_id = _proposal(repo, loop, workflow_id)
    _queue_rewrite(loop)
    candidate = loop.generate_candidate(proposal_id)
    assert loop.evaluate_proposal(proposal_id).status == "pending_approval"
    with pytest.raises(NotHumanError, match="named human"):
        loop.approve_proposal(proposal_id, machine_approver)
    new_workflow_id = loop.approve_proposal(proposal_id, "Alice Reviewer")
    assert new_workflow_id != workflow_id
    assert repo.get_version("text_summarizer", candidate).status is Status.STABLE
    assert repo.default_version("text_summarizer").version == "0.1"
    old = repo.graph.get_node(workflow_id)
    new = repo.graph.get_node(new_workflow_id)
    assert old is not None and old.props["active"] is False
    assert new is not None and new.props["active"] is True
    prefs = repo.graph.rels(
        start=repo.graph.find_nodes("QueryType", {"key": "text->text_summarizer"})[0].id,
        rel_type="PREFERS",
    )
    assert sum(rel.props.get("active", True) is True for rel in prefs) == 1
    routed = Router(repo).route("summarize the key points of this report")
    assert routed.plan is not None and routed.plan.goal_version == candidate


def test_rollback_repoints_to_prior_workflow_and_requires_human(feedback_context) -> None:
    repo, execution, loop = feedback_context
    workflow_id = execution.workflow_id
    loop.add_case(workflow_id, _case(
        "omit second sentence",
        CaseCheck(kind="must_not_contain", value="Beta launch"),
    ))
    loop.add_case(workflow_id, _case(
        "modality invariant",
        CaseCheck(kind="exact_field", field="modality", expected="text"),
    ))
    proposal_id = _proposal(repo, loop, workflow_id)
    _queue_rewrite(loop)
    candidate = loop.generate_candidate(proposal_id)
    assert loop.evaluate_proposal(proposal_id).status == "pending_approval"
    new_workflow_id = loop.approve_proposal(proposal_id, "Alice")
    with pytest.raises(NotHumanError, match="named human"):
        loop.rollback("text->text_summarizer", workflow_id, "system:rollback")
    loop.rollback("text->text_summarizer", workflow_id, "Alice")
    old = repo.graph.get_node(workflow_id)
    current = repo.graph.get_node(new_workflow_id)
    assert old is not None and old.props["active"] is True
    assert current is not None and current.props["active"] is False
    routed = Router(repo).route("summarize the key points of this report")
    assert routed.plan is not None and routed.plan.goal_version == "0.1"
    assert repo.get_version("text_summarizer", candidate).status is Status.STABLE


def test_human_rejection_rejects_candidate_and_keeps_old_workflow(feedback_context) -> None:
    repo, execution, loop = feedback_context
    proposal_id = _proposal(repo, loop, execution.workflow_id)
    _queue_rewrite(loop)
    candidate = loop.generate_candidate(proposal_id)
    with pytest.raises(NotHumanError, match="named human"):
        loop.reject_proposal(proposal_id, "system:reject", "not useful")
    with pytest.raises(FeedbackError, match="reason"):
        loop.reject_proposal(proposal_id, "Alice", " ")
    loop.reject_proposal(proposal_id, "Alice", "The rewrite is not useful.")
    assert repo.get_version("text_summarizer", candidate).status is Status.REJECTED
    assert repo.graph.get_node(proposal_id).props["status"] == "rejected"
    assert repo.default_version("text_summarizer").version == "0.1"


def test_goal_agent_loop_break_opens_proposal_and_deduplicates(seeded: AgentRepository) -> None:
    # A real repeated tool failure exercises the runner's existing loop-break condition.
    calls = {"total": 0}

    def fail(payload: Payload, params: dict[str, object]) -> Payload:
        calls["total"] += 1
        raise ToolError("temporary service failure")

    seeded.tools.register(ToolSpec(
        "feedback_failing_tool", "always fails", RiskTier.LOW,
        (Modality.TEXT,), Modality.TEXT, True, fail,
    ))
    seeded._sync_tools()
    manifest = AgentManifest(
        name="feedback_failure_agent",
        purpose="Translate text into Dutch.",
        usefulness="Translates Dutch text.",
        input_modalities=[Modality.TEXT],
        input_description="text",
        processing_description="calls the intentionally failing test tool",
        pipeline=[PipelineStep(kind="tool", name="feedback_failing_tool")],
        output_type=Modality.TEXT,
        output_description="translated text",
        example_queries=["translate this text into Dutch"],
    )
    record = seeded.create_agent(manifest, "alice")
    seeded.submit_for_approval(record.agent, record.version, "alice")
    seeded.approve(record.agent, record.version, "alice")
    runner = Runner(seeded, limits=ExecutionLimits(max_retries_per_tool=0))
    result = runner.execute_query("translate this text into Dutch")
    assert result.proposal_id is not None
    assert calls["total"] == 1
    proposal = seeded.graph.get_node(result.proposal_id)
    assert proposal is not None and proposal.props["trigger"].startswith("loop_break:")
    query_type = seeded.graph.find_nodes("QueryType", {"key": "text->feedback_failure_agent"})[0]
    assert not seeded.graph.rels(start=query_type.id, rel_type="PREFERS")
    assert ensure_open_proposal(
        seeded,
        str(proposal.props["workflow_id"]),
        record.agent,
        record.version,
        "loop_break:duplicate",
        datetime.now(UTC).isoformat(),
    ) == proposal.id
    assert len(seeded.graph.find_nodes("Proposal", {"workflow_id": proposal.props["workflow_id"]})) == 1
