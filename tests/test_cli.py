from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from agent_repo import cli
from agent_repo.graph.neo4j_store import Neo4jGraph
from agent_repo.graph.store import InMemoryGraph
from agent_repo.manifest import AgentManifest
from agent_repo.repository import AgentRepository
from agent_repo.seeds import seed_repository
from agent_repo.tools import builtin_registry


def _run(args: list[str], capsys: pytest.CaptureFixture[str]) -> str:
    result = cli.main(args)
    captured = capsys.readouterr()
    assert result == 0, captured.err
    return captured.out


def test_argparse_cli_persists_graph_and_supports_read_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEO4J_PASSWORD", "cli-password-must-not-print")
    graph_file = tmp_path / "agents.json"
    output = _run(["agents", "--graph-file", str(graph_file)], capsys)
    assert "text_summarizer" in output
    assert graph_file.is_file()

    versions = _run(["--graph-file", str(graph_file), "versions", "text_summarizer"], capsys)
    assert "text_summarizer@0.1: stable" in versions

    catalog = _run(["--graph-file", str(graph_file), "catalog"], capsys)
    assert "# Agent Catalog" in catalog

    cypher_path = tmp_path / "export.cypher"
    exported = _run(["--graph-file", str(graph_file), "cypher", "--output", str(cypher_path)], capsys)
    assert "Cypher export written" in exported
    cypher = cypher_path.read_text(encoding="utf-8")
    assert "text_summarizer" in cypher
    assert "cli-password-must-not-print" not in cypher
    assert "NEO4J_TEST_WIPE" not in cli.build_parser().format_help()


def test_cypher_output_redacts_configured_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = "cli-password-never-show-this"
    monkeypatch.setenv("NEO4J_PASSWORD", credential)
    graph = InMemoryGraph()
    graph.create_node("AgentRequest", {"description": f"unexpected value {credential}"})
    stdout = io.StringIO()
    status = cli.main(["cypher"], graph_store=graph, stdout=stdout)
    assert status == 0
    assert credential not in stdout.getvalue()
    assert "[REDACTED_CREDENTIAL]" in stdout.getvalue()


def test_cypher_stdout_redacts_env_credential(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    credential = "cli-cypher-env-credential-99887766"
    monkeypatch.setenv("NEO4J_PASSWORD", credential)
    graph_file = tmp_path / "leaky.json"
    graph = InMemoryGraph()
    graph.create_node("AgentRequest", {"description": f"unexpected value {credential}"})
    graph.save(graph_file)
    output = _run(["--graph-file", str(graph_file), "cypher"], capsys)
    assert credential not in output
    assert "[REDACTED_CREDENTIAL]" in output


def test_cypher_export_of_demo_graph_is_verbatim(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_REPO_HASH_SALT", "cli-demo-export-salt-long")
    graph_file = tmp_path / "demo-export.json"
    _run(["--graph-file", str(graph_file), "demo"], capsys)
    graph = InMemoryGraph.load(graph_file)
    hashes = [node.props["content_hash"] for node in graph.all_nodes() if "content_hash" in node.props]
    ids = [node.id for node in graph.all_nodes()] + [rel.id for rel in graph.all_rels()]
    assert hashes and ids
    cypher_path = tmp_path / "demo-export.cypher"
    _run(["--graph-file", str(graph_file), "cypher", "--output", str(cypher_path)], capsys)
    written = cypher_path.read_text(encoding="utf-8")
    stdout = _run(["--graph-file", str(graph_file), "cypher"], capsys)
    for text in (written, stdout):
        assert "[REDACTED_" not in text
    for value in hashes + ids:
        assert value in written


def test_stored_trace_tokens_used_is_int_matching_runner_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_REPO_HASH_SALT", "cli-tokens-salt-long-enough")
    graph_file = tmp_path / "tokens.json"
    _run([
        "--graph-file", str(graph_file), "ask",
        "summarize this report: Alpha result. Beta outcome.",
    ], capsys)
    graph = InMemoryGraph.load(graph_file)
    executions = [node for node in graph.all_nodes()
                  if node.label == "Execution" and "trace_json" in node.props]
    assert executions
    for execution in executions:
        events = json.loads(execution.props["trace_json"])
        detail_tokens = [event["detail"]["tokens_used"] for event in events
                         if "tokens_used" in event.get("detail", {})]
        assert detail_tokens
        assert all(isinstance(value, int) for value in detail_tokens)
        assert detail_tokens[-1] == execution.props["tokens_used"]


def test_argparse_errors_do_not_echo_credentials(capsys: pytest.CaptureFixture[str],
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    credential = "cli-credential-never-echo"
    monkeypatch.setenv("NEO4J_PASSWORD", credential)
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--password", credential, "agents"])
    captured = capsys.readouterr()
    assert exit_info.value.code == 2
    assert credential not in captured.err
    assert "[REDACTED_CREDENTIAL]" in captured.err


def test_ask_explain_and_cli_feedback_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_REPO_HASH_SALT", "cli-test-salt-long-enough")
    graph_file = tmp_path / "workflow.json"
    output = _run([
        "--graph-file", str(graph_file), "ask",
        "summarize this report: Alpha result. Beta outcome.",
    ], capsys)
    execution_id = next(line.split(": ", 1)[1] for line in output.splitlines() if line.startswith("Execution: "))
    assert "Status: succeeded" in output

    graph = InMemoryGraph.load(graph_file)
    execution = graph.get_node(execution_id)
    assert execution is not None
    workflow_id = graph.rels(start=execution_id, rel_type="RAN")[0].end

    explanation = _run(["--graph-file", str(graph_file), "explain", execution_id], capsys)
    assert "Execution:" in explanation and "text_summarizer" in explanation

    _run([
        "--graph-file", str(graph_file), "case", "add", workflow_id,
        "--name", "exact modality", "--payload", "Alpha result. Beta outcome.",
        "--exact", 'modality="text"',
    ], capsys)
    _run([
        "--graph-file", str(graph_file), "case", "add", workflow_id,
        "--name", "omit beta", "--payload", "Alpha result. Beta outcome.",
        "--must-not-contain", "Beta outcome",
    ], capsys)
    for person in ("cli-user-one", "cli-user-two", "cli-user-three"):
        _run([
            "--graph-file", str(graph_file), "feedback", execution_id,
            "--user-id", person, "--rating", "-1", "--comment", "The result missed context.",
        ], capsys)
    proposals = _run(["--graph-file", str(graph_file), "proposals"], capsys)
    assert "trigger=negative_feedback" in proposals
    proposal_id = proposals.split(": ", 1)[0]
    case_output = _run(["--graph-file", str(graph_file), "case", "run", workflow_id], capsys)
    assert "failed" in case_output and "passed" in case_output
    rewrite = json.dumps({
        "system_prompt": "Summarize the facts in one sentence.",
        "pipeline": [{"kind": "tool", "name": "text_summarize", "params": {"max_sentences": 1}}],
    })
    generated = _run([
        "--graph-file", str(graph_file), "proposals", "generate", proposal_id,
        "--rewrite-json", rewrite,
    ], capsys)
    assert "Candidate version:" in generated
    evaluation = _run(["--graph-file", str(graph_file), "proposals", "evaluate", proposal_id], capsys)
    assert "Evaluation: pending_approval" in evaluation
    approved = _run(["--graph-file", str(graph_file), "approve", proposal_id, "--by", "CLI Test Reviewer"], capsys)
    assert "Proposal approved" in approved

    graph = InMemoryGraph.load(graph_file)
    query_type = graph.find_nodes("QueryType", {"key": "text->text_summarizer"})[0]
    workflow_id = graph.rels(start=query_type.id, rel_type="PREFERS")[0].end
    rejection = graph.create_node("Proposal", {
        "workflow_id": workflow_id,
        "query_type_key": "text->text_summarizer",
        "agent": "text_summarizer",
        "base_version": "0.1",
        "trigger": "cli_rejection_test",
        "status": "open",
        "created_at": "2026-10-19T00:00:00+00:00",
    })
    graph.create_rel("HAS_PROPOSAL", workflow_id, rejection.id)
    graph.save(graph_file)
    rejected = _run([
        "--graph-file", str(graph_file), "reject", rejection.id,
        "--by", "CLI Test Reviewer", "--reason", "The result does not satisfy the case.",
    ], capsys)
    assert "Proposal rejected" in rejected


def test_demo_runs_ordered_workflow_and_both_rewrite_gate_outcomes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_REPO_HASH_SALT", "cli-demo-salt-long-enough")
    output = _run(["--graph-file", str(tmp_path / "demo.json"), "demo"], capsys)
    expected_sections = (
        "1. summarize text",
        "2. summarize audio file",
        "3. classify an image",
        "4. analyze a file",
        "5. generate an image",
        "6a. HIGH-risk email denied",
        "6b. HIGH-risk email allowed",
        "7. no-match breakpoint",
        "8a. missing attachment breakpoint",
        "8b. missing file breakpoint",
        "9. same query type reused",
        "10. three distinct users",
        "11. human approval",
        "12. LangGraph plan-approval pause",
    )
    positions = [output.index(section) for section in expected_sections]
    assert positions == sorted(positions)
    assert "MOCK LLM: scripted rewrite" in output
    assert "evaluation: pending_approval" in output
    assert "Useless rewrite evaluation: rejected_by_gate" in output
    assert "Breakpoint: missing_input" in output
    assert "Breakpoint: no_match" in output


def test_demo_end_to_end_shows_stored_label_gate_rejection_and_real_breakpoint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_REPO_HASH_SALT", "cli-demo-e2e-salt-long-enough")
    graph_file = tmp_path / "demo-e2e.json"
    output = _run(["--graph-file", str(graph_file), "demo"], capsys)
    assert "MOCK LLM: scripted rewrite" in output
    assert "rejected_by_gate" in output
    assert "Breakpoint: missing_input" in output
    graph = InMemoryGraph.load(graph_file)
    labels = {node.props.get("rewrite_label") for node in graph.find_nodes("Proposal")}
    assert "MOCK LLM: scripted rewrite" in labels


def _seed_graph_with_secret_purpose(graph_file: Path, secret: str) -> None:
    repo = AgentRepository(InMemoryGraph(), builtin_registry())
    seed_repository(repo, "Secret Setup Human")
    base = repo.default_version("text_summarizer")
    updated = base.manifest.model_dump(mode="python")
    updated["purpose"] = f"{base.manifest.purpose} Special codeword: {secret}."
    record = repo.create_version(
        "text_summarizer",
        AgentManifest.model_validate(updated),
        "Secret Setup Human",
        "carry the demo redaction codeword",
        parent_version=base.version,
    )
    repo.submit_for_approval("text_summarizer", record.version, "Secret Setup Human")
    repo.approve("text_summarizer", record.version, "Secret Setup Human")
    repo.set_default_version("text_summarizer", record.version, "Secret Setup Human")
    repo.graph.save(graph_file)


def test_demo_redacts_a_secret_looking_string_from_demo_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_REPO_HASH_SALT", "cli-demo-secret-salt-long-enough")
    secret = "zephyr-lantern-48271-quartz"
    monkeypatch.setenv("DEMO_LEAKED_SECRET", secret)
    graph_file = tmp_path / "demo-secret.json"
    _seed_graph_with_secret_purpose(graph_file, secret)
    output = _run(["--graph-file", str(graph_file), "demo"], capsys)
    assert secret not in output
    assert "[REDACTED_CREDENTIAL]" in output


@pytest.mark.neo4j_smoke
def test_cli_agents_command_on_aura_graph(
    graph, capsys: pytest.CaptureFixture[str]
) -> None:
    if isinstance(graph, InMemoryGraph):
        pytest.skip("Aura CLI smoke test runs only against the Neo4j backend.")
    assert isinstance(graph, Neo4jGraph)
    stdout = io.StringIO()
    status = cli.main(["agents"], graph_store=graph, stdout=stdout)
    assert status == 0
    assert "text_summarizer" in stdout.getvalue()
