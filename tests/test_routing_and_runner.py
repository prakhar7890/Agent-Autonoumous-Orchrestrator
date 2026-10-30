"""Milestone 2 tests: routing from facts, modality-word rule, BFS prerequisite chain with
transitive HIGH-risk check, all 8 breakpoints, alternative_skipped, workflow memory & reuse,
execution_subgraph + renderer, and Cypher export.

Every behavioural test uses the `seeded` fixture, which is parametrised over `memory` and `neo4j`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_repo.config import ExecutionLimits
from agent_repo.execution.runner import Runner
from agent_repo.graph.views import execution_subgraph, export_cypher, render_execution_subgraph
from agent_repo.manifest import AgentManifest, Modality, PipelineStep, RiskTier
from agent_repo.repository import AgentRepository
from agent_repo.routing import BreakReason, Router
from agent_repo.tools import AuthorityGrant, CallContext, Payload, ToolError, ToolSpec
from tests.conftest import MAINTAINER


# ---------------------------------------------------------------- 1. ROUTING FROM FACTS & CHAIN INSERTION

@pytest.mark.neo4j_smoke
def test_routing_modalities_and_auto_chain_insertion(seeded: AgentRepository, tmp_path: Path) -> None:
    router = Router(seeded)

    # 1) Plain text -> text_summarizer directly
    r_text = router.route("summarize the key points of this article")
    assert r_text.plan is not None and r_text.break_reason is None
    assert [s.agent for s in r_text.plan.steps] == ["text_summarizer"]
    assert r_text.plan.payload_modality is Modality.TEXT

    # 2) Audio attachment + "summarize this audio recording" -> drops redundant "audio"/"recording"
    #    from intent, picks text_summarizer as goal, and BFS-inserts chainable audio_transcriber!
    audio_file = tmp_path / "call.wav"
    audio_file.write_bytes(b"RIFF....")
    r_audio = router.route("summarize this audio recording", attachment_path=str(audio_file))
    assert r_audio.plan is not None
    assert r_audio.plan.payload_modality is Modality.AUDIO
    assert "audio" in r_audio.plan.dropped_modality_words
    assert [(s.role, s.agent) for s in r_audio.plan.steps] == [
        ("prerequisite", "audio_transcriber"),
        ("goal", "text_summarizer"),
    ]

    # 3) Image attachment + "classify this image" -> drops "image", routes to image_classifier
    img_file = tmp_path / "photo.png"
    img_file.write_bytes(b"\x89PNG")
    r_img = router.route("classify this image and label what it shows", attachment_path=str(img_file))
    assert r_img.plan is not None
    assert [s.agent for s in r_img.plan.steps] == ["image_classifier"]
    assert "image" in r_img.plan.dropped_modality_words

    # 4) File attachment + "analyze this csv file" -> file_analyzer
    csv_file = tmp_path / "data.csv"
    csv_file.write_text("a,b\n1,2\n", encoding="utf-8")
    r_file = router.route("analyze this csv file and give statistics", attachment_path=str(csv_file))
    assert r_file.plan is not None
    assert [s.agent for s in r_file.plan.steps] == ["file_analyzer"]

    # 5) Modality-word rule: "generate an image of a report" with TEXT payload (no image attached).
    #    "image" MUST NOT be dropped because the payload is text, not image!
    r_gen = router.route("generate an image of a quarterly report")
    assert r_gen.plan is not None
    assert r_gen.plan.payload_modality is Modality.TEXT
    assert r_gen.plan.dropped_modality_words == []
    assert [s.agent for s in r_gen.plan.steps] == ["image_generator"]


def test_bfs_never_inserts_chainable_prerequisite_reaching_high_risk(
    seeded: AgentRepository, tmp_path: Path
) -> None:
    """Even if a human creates a chainable agent that reaches a HIGH-risk tool (directly or via sub-agent),
    the router's BFS prerequisite search must refuse to auto-insert it.
    """
    # Create a composite chainable agent file -> text that delegates to email_sender (HIGH risk)
    unsafe_pre = AgentManifest(
        name="unsafe_file_converter",
        purpose="Convert a file into text and mail it.",
        usefulness="Extracts text from files.",
        input_modalities=[Modality.FILE],
        input_description="attached file",
        processing_description="calls email_sender sub-agent",
        pipeline=[PipelineStep(kind="agent", name="email_sender", version="0.1")],
        output_type=Modality.TEXT,
        output_description="text",
        example_queries=["convert file"],
        chainable=True,
    )
    rec = seeded.create_agent(unsafe_pre, author=MAINTAINER)
    seeded.submit_for_approval(rec.agent, rec.version, MAINTAINER)
    seeded.approve(rec.agent, rec.version, MAINTAINER)
    assert seeded.reaches_high_risk("unsafe_file_converter", "0.1") is True

    f = tmp_path / "doc.txt"
    f.write_text("some content", encoding="utf-8")
    router = Router(seeded)
    # Asking to summarize a FILE attachment: text_summarizer needs TEXT, payload is FILE.
    # The only FILE->TEXT chainable agent is unsafe_file_converter (reaches HIGH risk), so BFS must reject it -> missing_input!
    outcome = router.route("summarize the key points", attachment_path=str(f))
    assert outcome.plan is None
    assert outcome.break_reason is BreakReason.MISSING_INPUT


# ---------------------------------------------------------------- 2. WORKFLOW MEMORY, REUSE & SUBGRAPH

@pytest.mark.neo4j_smoke
def test_workflow_memory_reuse_and_execution_subgraph(seeded: AgentRepository, tmp_path: Path) -> None:
    runner = Runner(seeded)
    audio_file = tmp_path / "meeting.wav"
    audio_file.write_bytes(b"RIFF....")
    (tmp_path / "meeting.transcript.txt").write_text(
        "Alice presented the Q3 roadmap. Bob confirmed the launch date is Friday. Everyone agreed.",
        encoding="utf-8",
    )

    # First execution of audio -> text_summarizer composes the workflow and stores it on QueryType
    res1 = runner.execute_query("summarize this audio recording", attachment_path=str(audio_file))
    assert res1.status == "succeeded"
    assert res1.plan is not None and res1.plan.reused_workflow_id is None
    assert res1.workflow_id is not None

    # Second similar query with same payload modality and same goal agent reuses the stored workflow
    res2 = runner.execute_query("give me a short summary of this audio", attachment_path=str(audio_file))
    assert res2.status == "succeeded"
    assert res2.plan is not None
    assert res2.plan.reused_workflow_id == res1.workflow_id

    # Overlapping words ("audio", "this") but a DIFFERENT goal agent ("transcribe this audio" -> audio_transcriber)
    # MUST NOT reuse the audio->text_summarizer workflow!
    res3 = runner.execute_query("transcribe this audio recording", attachment_path=str(audio_file))
    assert res3.status == "succeeded"
    assert res3.plan is not None
    assert res3.plan.goal_agent == "audio_transcriber"
    assert res3.plan.reused_workflow_id is None
    assert res3.workflow_id != res1.workflow_id

    # Verify execution_subgraph and text renderer on composite agent (meeting_note_taker) and chained run
    res_comp = runner.execute_query("take meeting notes for this recording", attachment_path=str(audio_file))
    assert res_comp.status == "succeeded"
    sub = execution_subgraph(seeded, res_comp.execution_id)
    assert sub.query_type_key == "audio->meeting_note_taker"
    assert len(sub.steps) == 1
    assert sub.steps[0].agent == "meeting_note_taker"
    assert sub.steps[0].reason
    # Reachable tools must include sub-agent tools!
    assert sorted(sub.steps[0].reachable_tools) == [
        "audio_transcribe (low, STUB) via audio_transcriber@0.1",
        "text_summarize (low) via text_summarizer@0.1",
    ]
    rendered = render_execution_subgraph(sub)
    assert "meeting_note_taker@0.1" in rendered
    assert "audio_transcribe (low, STUB)" in rendered

    cypher = export_cypher(seeded.graph)
    assert "MERGE (n:Execution:GraphNode" in cypher
    assert "MERGE (a)-[r:INCLUDES" in cypher


# ---------------------------------------------------------------- 3. ALL 8 BREAKPOINTS & ALTERNATIVE_SKIPPED

@pytest.mark.neo4j_smoke
def test_real_breakpoints_no_match_missing_input_and_permission_denied(
    seeded: AgentRepository, tmp_path: Path
) -> None:
    runner = Runner(seeded)

    # 1) NO_MATCH: unrelated query -> Escalation + data-only draft AgentRequest (never routable)
    r_nomatch = runner.execute_query("bake a sourdough pizza at 500 degrees")
    assert r_nomatch.status == "broken"
    assert r_nomatch.break_reason is BreakReason.NO_MATCH
    assert r_nomatch.escalation_id is not None
    assert r_nomatch.agent_request_id is not None
    assert r_nomatch.clarifying_question
    req_node = seeded.graph.get_node(r_nomatch.agent_request_id)
    assert req_node is not None and req_node.props["status"] == "draft" and req_node.props["routable"] is False
    # Re-running the same unmatched query still cannot route to the draft
    assert runner.execute_query("bake a sourdough pizza at 500 degrees").break_reason is BreakReason.NO_MATCH

    # 2) MISSING_INPUT: real missing attachment ("classify this photo" with no file attached)
    r_missing = runner.execute_query("classify this picture and label what is in the photo")
    assert r_missing.status == "broken"
    assert r_missing.break_reason is BreakReason.MISSING_INPUT
    assert r_missing.escalation_id is not None

    # Also test non-existent file path
    r_missing_path = runner.execute_query(
        "analyze this file", attachment_path=str(tmp_path / "does_not_exist.csv")
    )
    assert r_missing_path.status == "broken"
    assert r_missing_path.break_reason is BreakReason.MISSING_INPUT

    # 3) PERMISSION_DENIED: HIGH-risk email_sender without grant -> denied (hard stop), then allowed with human grant
    r_denied = runner.execute_query("send an email to the team with the update")
    assert r_denied.status == "broken"
    assert r_denied.break_reason is BreakReason.PERMISSION_DENIED
    assert r_denied.escalation_id is not None

    grant_ctx = CallContext(
        actor="bob",
        grants=(AuthorityGrant(tool="email_send", granted_by="carol", reason="approved status email"),),
    )
    r_allowed = runner.execute_query("send an email to the team with the update", ctx=grant_ctx)
    assert r_allowed.status == "succeeded"


def test_runtime_breakpoints_retries_alternative_skipped_and_limits(seeded: AgentRepository) -> None:
    """Test max_retries, alternative_skipped (when an alternative shares the failing tool),
    output_invalid, max_steps, timeout, token_budget, and plan approval decline.
    """
    # Register a test tool that always fails with ToolError, and two agents that both use it
    calls = {"flaky": 0}

    def failing_tool(p: Payload, params: dict[str, object]) -> Payload:
        calls["flaky"] += 1
        raise ToolError("upstream service 503")

    def empty_tool(p: Payload, params: dict[str, object]) -> Payload:
        return Payload(modality=Modality.TEXT, text="   ")

    seeded.tools.register(
        ToolSpec("flaky_translate", "test failing tool", RiskTier.LOW, (Modality.TEXT,), Modality.TEXT, True, failing_tool)
    )
    seeded.tools.register(
        ToolSpec("empty_output_tool", "returns blank text", RiskTier.LOW, (Modality.TEXT,), Modality.TEXT, True, empty_tool)
    )
    seeded._sync_tools()  # noqa: SLF001

    for name in ("translator_primary", "translator_backup"):
        m = AgentManifest(
            name=name,
            purpose="Translate text into French and Spanish.",
            usefulness="Translates foreign language documents.",
            input_modalities=[Modality.TEXT],
            input_description="text",
            processing_description="calls flaky_translate",
            pipeline=[PipelineStep(kind="tool", name="flaky_translate")],
            output_type=Modality.TEXT,
            output_description="translated text",
            example_queries=["translate this text into french"],
        )
        rec = seeded.create_agent(m, author=MAINTAINER)
        seeded.submit_for_approval(rec.agent, rec.version, MAINTAINER)
        seeded.approve(rec.agent, rec.version, MAINTAINER)

    # 4) MAX_RETRIES + ALTERNATIVE_SKIPPED + Proposal filed on goal agent break
    runner = Runner(seeded, limits=ExecutionLimits(max_retries_per_tool=2, max_alternative_workflows=1))
    r_retry = runner.execute_query("translate this text into french")
    assert r_retry.status == "broken"
    assert r_retry.break_reason is BreakReason.MAX_RETRIES
    # 1 initial try + 2 retries = 3 calls total. The backup agent uses the SAME exhausted tool,
    # so it must be skipped without calling flaky_translate a 4th time!
    assert calls["flaky"] == 3
    assert any(e.event == "alternative_skipped" for e in r_retry.trace)
    assert r_retry.proposal_id is not None  # Goal agent failed -> improvement proposal started

    # 5) OUTPUT_INVALID: agent returns empty text
    bad_m = AgentManifest(
        name="broken_formatter",
        purpose="Format a bibliography citation in APA style.",
        usefulness="Formats academic citations.",
        input_modalities=[Modality.TEXT],
        input_description="citation text",
        processing_description="calls empty_output_tool",
        pipeline=[PipelineStep(kind="tool", name="empty_output_tool")],
        output_type=Modality.TEXT,
        output_description="formatted citation",
        example_queries=["format bibliography citation in apa style"],
    )
    rec_bad = seeded.create_agent(bad_m, author=MAINTAINER)
    seeded.submit_for_approval(rec_bad.agent, rec_bad.version, MAINTAINER)
    seeded.approve(rec_bad.agent, rec_bad.version, MAINTAINER)

    r_invalid = runner.execute_query("format bibliography citation in apa style")
    assert r_invalid.status == "broken"
    assert r_invalid.break_reason is BreakReason.OUTPUT_INVALID

    # 6) MAX_STEPS: limit max_steps=0 so the first step trips max_steps
    runner_steps = Runner(seeded, limits=ExecutionLimits(max_steps=0))
    r_steps = runner_steps.execute_query("summarize this text: hello world.")
    assert r_steps.status == "broken"
    assert r_steps.break_reason is BreakReason.MAX_STEPS

    # 7) TIMEOUT: fake timer that advances past timeout_seconds
    ticks = iter([0.0, 5.0, 50.0, 55.0])
    runner_timeout = Runner(
        seeded,
        limits=ExecutionLimits(timeout_seconds=10.0),
        timer=lambda: next(ticks, 60.0),
    )
    r_timeout = runner_timeout.execute_query("summarize this text: hello world.")
    assert r_timeout.status == "broken"
    assert r_timeout.break_reason is BreakReason.TIMEOUT

    # 8) TOKEN_BUDGET: token budget exceeded after tool call
    runner_tokens = Runner(
        seeded,
        limits=ExecutionLimits(token_budget=5),
        token_estimator=lambda payload, step: 500,
    )
    r_tokens = runner_tokens.execute_query("summarize this text: hello world.")
    assert r_tokens.status == "broken"
    assert r_tokens.break_reason is BreakReason.TOKEN_BUDGET

    # 9) Plan approval decline (`finish_declined`) and trace secret scrubbing
    r_declined = runner.execute_query(
        "summarize this text for bob@example.com with api_key=secret123",
        approve_plan=lambda p: False,
    )
    assert r_declined.status == "declined"
    trace_dump = " ".join(str(e.detail) for e in r_declined.trace)
    assert "bob@example.com" not in trace_dump and "secret123" not in trace_dump
    assert "[REDACTED_EMAIL]" in trace_dump and "[REDACTED_SECRET]" in trace_dump


def test_symmetric_modality_word_normalisation_and_payload_compatible_margin(
    seeded: AgentRepository,
) -> None:
    """Modality words normalise symmetrically between query and metadata terms, and candidates within
    the 0.15 score margin that accept the payload (directly or via chain) are preferred over
    incompatible candidates while keeping missing_input when no compatible candidate is within margin.
    """
    router = Router(seeded)

    # Symmetric modality normalisation: 'paragraph' normalises to 'text', matching text_summarizer
    r_para = router.route("pull out the main takeaway from this paragraph")
    assert r_para.plan is not None and r_para.plan.goal_agent == "text_summarizer"

    # Payload compatibility within 0.15 margin: 'text' matches both audio_transcriber (needs audio)
    # and text_summarizer (accepts text) within 0.15; text_summarizer is chosen.
    r_compat = router.route("what are the highlights of this text")
    assert r_compat.plan is not None and r_compat.plan.goal_agent == "text_summarizer"

    # When no payload-compatible candidate is within the 0.15 margin, missing_input is preserved.
    r_incompat = router.route("classify this picture and label what is in the photo")
    assert r_incompat.plan is None and r_incompat.break_reason is BreakReason.MISSING_INPUT


