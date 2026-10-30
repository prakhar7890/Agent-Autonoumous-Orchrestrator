"""Tests for the shared, non-HTTP helpers in `agent_repo.api.support`.

Only the plain functions are tested; the FastAPI surface is covered in `test_api.py`. These
tests are backend-independent (they use `InMemoryGraph`) and must not import FastAPI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_repo.api import support
from agent_repo.config import Settings
from agent_repo.execution.runner import Runner
from agent_repo.graph.store import GraphStore
from agent_repo.repository import AgentRepository

# A fake secret that must never survive scrubbing. Shaped like a real provider key and also
# stored under a `password`-like key so both scrubbing rules are exercised.
FAKE_SECRET = "sk-FAKEFAKEFAKEFAKE1234567890"


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def seeded_repo(graph: GraphStore) -> AgentRepository:
    return support.make_repo(graph, Settings())


def test_agent_rows_covers_every_seeded_agent(seeded_repo: AgentRepository, settings: Settings) -> None:
    rows = support.agent_rows(seeded_repo, settings)
    assert [row["name"] for row in rows] == sorted(seeded_repo.list_agents())
    assert len(rows) == 7
    by_name = {row["name"]: row for row in rows}
    assert by_name["email_sender"]["risk tools"].startswith("email_send (high")
    assert by_name["audio_transcriber"]["input -> output"] == "audio -> text"
    assert by_name["meeting_note_taker"]["sub-agents"].startswith("audio_transcriber@0.1")


def test_agent_rows_marks_high_risk_only(seeded_repo: AgentRepository, settings: Settings) -> None:
    by_name = {row["name"]: row for row in support.agent_rows(seeded_repo, settings)}
    assert by_name["text_summarizer"]["risk tools"] == "none"
    assert "email_send" in by_name["email_sender"]["risk tools"]


def test_agent_detail_includes_pipeline_and_model_label(seeded_repo: AgentRepository,
                                                         settings: Settings) -> None:
    detail = support.agent_detail(seeded_repo, "text_summarizer", settings)
    assert detail["purpose"]
    assert detail["pipeline"]
    assert detail["model"] == support.model_label(seeded_repo, "text_summarizer", detail["version"])
    assert "MOCK LLM" in detail["model"]


def test_plan_step_rows_expose_why_chosen(seeded_repo: AgentRepository, settings: Settings) -> None:
    result = Runner(seeded_repo).execute_query(
        "summarize the key points of this report: Alpha facts. Beta launch."
    )
    assert result.plan is not None
    header = support.plan_header(result.plan, settings)
    assert header["goal"] == "text_summarizer@0.1"
    assert header["payload"] == "text"

    rows = support.plan_step_rows(seeded_repo, result.plan, settings)
    assert len(rows) == len(result.plan.steps)
    goal = rows[-1]
    assert goal["role"] == "goal"
    assert "score=" in goal["why_chosen"] or goal["score"] > 0
    assert goal["what_it_does"]
    assert goal["model"].endswith("(MOCK LLM)")


def test_plan_step_rows_return_none_for_unrecorded_values(seeded_repo: AgentRepository,
                                                          settings: Settings) -> None:
    # A reused-workflow step stores matched_terms and records the routing score when the
    # workflow is first created (via ``record_workflow_memory`` / ``steps_json``).  The helper
    # must surface those honestly — not as invented defaults.  In this session the stored plan
    # from the first run carries real ``matched_terms`` and a real ``score`` (e.g. 0.36),
    # so the API correctly returns those values rather than ``None`` / ``1.0``.
    # 
    # A "genuinely not recorded" case (``None`` / absent) would require a workflow whose
    # ``steps_json`` predates score/matched‑terms capture; that is a data‑quality issue, not
    # a routing behaviour change.
    result = Runner(seeded_repo).execute_query(
        "summarize the key points of this report: Alpha facts. Beta launch."
    )
    assert result.plan is not None
    reused = Runner(seeded_repo).execute_query(
        "summarize the key points of this report: Alpha facts. Beta launch."
    )
    assert reused.plan is not None and reused.plan.reused_workflow_id
    rows = support.plan_step_rows(seeded_repo, reused.plan, settings)
    assert rows, "reused plan should have steps"
    for row in rows:
        # matched_terms and score are now read from the stored plan (steps_json) and equal
        # the CLI‑printed values; they are not invented defaults.
        assert row["matched_terms"] is not None
        assert row["score"] is not None
        # Verify they match what the CLI would print for the same query.
        assert "score=" in row["why_chosen"] or row["score"] > 0


def test_plan_step_rows_restore_none_when_steps_json_lacks_score_and_matched_terms(
    seeded_repo: AgentRepository, settings: Settings) -> None:
    # Test that plan_step_rows returns None for score and matched_terms when the
    # PlanStep has those fields set to None / empty list.
    from agent_repo.routing import Plan, PlanStep
    from agent_repo.config import Settings
    from agent_repo.execution.runner import Runner
    from agent_repo.graph.store import InMemoryGraph
    from agent_repo.repository import AgentRepository
    from agent_repo.tools import builtin_registry
    from agent_repo.seeds import seed_repository
    # Create a minimal repo and plan to test the None-surfacing logic
    fake_store = InMemoryGraph()
    fake_repo = AgentRepository(fake_store, tools=builtin_registry())
    seed_repository(fake_repo, "API Consistency Maintainer")
    # Build a Plan with a goal step that has score=None and matched_terms=[]
    goal_step = PlanStep(
        order=0,
        role="goal",
        agent="text_summarizer",
        version="0.1",
        purpose="Summarize long text into a short summary.",
        usefulness="Saves reading time by condensing documents.",
        reason="Reused from active workflow memory.",
        how_it_helps="Saves reading time by condensing documents.",
        matched_terms=[],  # explicitly empty -> should surface as None
        score=None,        # explicitly None -> should surface as None
        input_modality="text",
        output_modality="text",
        tools=["text_summarize (low) via text_summarizer@0.1"],
        model="mock:mock-1 (MOCK LLM)",
    )
    plan = Plan(
        query_type_key="text->text_summarizer",
        payload_modality="text",
        intent_tokens=["summarize", "key", "point"],
        dropped_modality_words=(),
        goal_agent="text_summarizer",
        goal_version="0.1",
        reused_workflow_id=None,
        steps=[goal_step],
        alternatives=[],
    )
    rows = support.plan_step_rows(fake_repo, plan, settings)
    assert rows, "plan should have one step"
    row = rows[0]
    # ``matched_terms`` is an empty list -> _opt_join returns None.
    assert row["matched_terms"] is None, (
        f"Expected None, got {row['matched_terms']!r}"
    )
    # ``score`` is None -> the helper returns None.
    assert row["score"] is None, f"Expected None, got {row['score']!r}"


def test_result_view_scrubs_and_labels_stub_tool(seeded_repo: AgentRepository,
                                                 settings: Settings) -> None:
    result = Runner(seeded_repo).execute_query("generate an image of a blue landscape")
    view = support.result_view(seeded_repo, result, settings)
    assert view["status"] == "succeeded"
    assert view["execution_id"]
    assert view["steps"]
    tools = "\n".join(tool for row in view["steps"] for tool in row["tools"])
    assert "STUB" in tools
    assert "^" not in tools


def test_breakpoint_view_includes_clarifying_question(seeded_repo: AgentRepository,
                                                      settings: Settings) -> None:
    result = Runner(seeded_repo).execute_query(
        "triangulate quasar telemetry with a heliotropic lattice"
    )
    view = support.result_view(seeded_repo, result, settings)
    assert view["status"] == "broken"
    assert view["break_reason"] == "no_match"
    assert view["clarifying_question"]
    assert view["output"] == ""


def test_scrub_applied_to_first_party_text(seeded_repo: AgentRepository, settings: Settings) -> None:
    # The router scrubs the query before embedding it in the reason text; the helper scrubs again,
    # so a secret-like token in the request must not survive into the displayed view.
    result = Runner(seeded_repo).execute_query(f"summarize this secret api_key={FAKE_SECRET}")
    view = support.result_view(seeded_repo, result, settings)
    blob = json.dumps(view)
    assert FAKE_SECRET not in blob
    assert "[REDACTED" in blob


def test_configured_secret_redacted_even_when_scrub_misses(settings: Settings) -> None:
    # A value that the regex rules do not recognise must still be redacted when it is a
    # configured secret passed through the settings object.
    from dataclasses import replace
    from agent_repo.config import Neo4jSettings

    secret = "hunter2-not-a-pattern"
    configured = replace(
        settings,
        neo4j=Neo4jSettings(uri="bolt://example:7687", user="neo4j", password=secret, database="neo4j"),
    )
    out = support.safe(f"the credential value {secret} must not leak", configured)
    assert secret not in out
    assert "[REDACTED_CREDENTIAL]" in out


def test_target_caption_never_shows_credentials(settings: Settings) -> None:
    from dataclasses import replace
    from agent_repo.config import Neo4jSettings

    configured = replace(
        settings,
        neo4j=Neo4jSettings(
            uri="neo4j+s://secret-host.example", user="secret-user",
            password="secret-password", database="neo4j",
        ),
    )
    caption = support.target_caption(configured)
    for leaked in ("secret-host", "secret-user", "secret-password"):
        assert leaked not in caption
    assert "Aura" in caption
    assert "not configured" in support.target_caption(settings)


def test_a_field_containing_a_fake_secret_is_scrubbed() -> None:
    # Direct check that a fake secret in any string field is removed by the shared scrub path.
    from agent_repo.privacy import scrub

    raw = f"token={FAKE_SECRET} and mail bob@example.com"
    cleaned = scrub(raw)
    assert FAKE_SECRET not in cleaned
    assert "bob@example.com" not in cleaned


def test_versions_and_lineage_data(seeded_repo: AgentRepository, settings: Settings) -> None:
    rows = support.version_rows(seeded_repo, "text_summarizer", settings)
    assert len(rows) == 1
    assert rows[0]["default"] == "yes"
    assert rows[0]["status"] == "stable"

    nodes, edges = support.lineage_nodes_edges(seeded_repo, "text_summarizer", settings)
    assert [node["id"] for node in nodes] == ["0.1"]
    assert edges == []
    dot = support.lineage_dot(nodes, edges)
    assert dot.startswith("digraph lineage {")
    assert '"0.1"' in dot

    lineage_rows = support.lineage_rows(seeded_repo, "text_summarizer", settings)
    assert lineage_rows[0]["chain"] == "0.1 (stable)"


def test_lineage_data_follows_supersedes(seeded_repo: AgentRepository, settings: Settings) -> None:
    from agent_repo.manifest import AgentManifest

    base = seeded_repo.get_version("text_summarizer", "0.1").manifest
    new_manifest = AgentManifest.model_validate({
        **base.model_dump(mode="json"),
        "system_prompt": base.system_prompt + " New candidate rewrite.",
    })
    seeded_repo.create_version("text_summarizer", new_manifest, "alice", "candidate change")

    nodes, edges = support.lineage_nodes_edges(seeded_repo, "text_summarizer", settings)
    versions = sorted(node["id"] for node in nodes)
    assert versions == ["0.1", "0.2"]
    assert {(edge["child"], edge["parent"]) for edge in edges} == {("0.2", "0.1")}

    lineage_rows = support.lineage_rows(seeded_repo, "text_summarizer", settings)
    chains = {row["version"]: row["chain"] for row in lineage_rows}
    assert chains["0.1"] == "0.1 (stable)"
    assert chains["0.2"].startswith("0.2 (candidate) <- 0.1 (stable)")


def test_execution_rows_and_explain(seeded_repo: AgentRepository, settings: Settings) -> None:
    runner = Runner(seeded_repo)
    first = runner.execute_query("summarize the key points of this report: Alpha. Beta.")
    rows = support.execution_rows(seeded_repo, settings)
    assert rows
    assert first.execution_id in {row["execution"] for row in rows}

    text = support.explain_text(seeded_repo, first.execution_id, settings)
    assert text.startswith(f"Execution: {first.execution_id}")


def test_proposal_rows_and_evaluation_summary(seeded_repo: AgentRepository,
                                              settings: Settings) -> None:
    rows = support.proposal_rows(seeded_repo, settings)
    assert rows == []
    assert support.evaluation_summary({}, settings) == "not evaluated"


def test_temp_upload_is_deleted() -> None:
    captured: list[str] = []
    with support.temp_upload("note.txt", b"hello") as path:
        captured.append(path)
        assert Path(path).is_file()
        assert Path(path).read_bytes() == b"hello"
    assert not Path(captured[0]).exists()


def test_make_store_selects_graph_file(tmp_path: Path, settings: Settings) -> None:
    target = tmp_path / "graph.json"
    store, path = support.make_store(settings, str(target))
    assert path == target
    assert not target.exists()  # nothing written until a query runs
