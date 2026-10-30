"""Argparse command line for the agent repository (Milestone 5)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from dotenv import dotenv_values
from pydantic import ValidationError

from agent_repo.actors import NotHumanError
from agent_repo.config import Settings
from agent_repo.execution.engines import LangGraphEngine, PendingApproval, PlainEngine
from agent_repo.execution.runner import ExecutionResult, Runner
from agent_repo.feedback import AutomaticCase, CaseCheck, FeedbackError, FeedbackLoop, ensure_open_proposal
from agent_repo.graph.neo4j_store import Neo4jConnectionError, Neo4jGraph
from agent_repo.graph.store import GraphError, GraphStore, InMemoryGraph
from agent_repo.graph.views import execution_subgraph, export_cypher, render_execution_subgraph
from agent_repo.llm import LLMError, LLMProvider, MockLLM, make_llm
from agent_repo.manifest import AgentManifest, Modality
from agent_repo.privacy import UserIdHasher, scrub
from agent_repo.repository import AgentRepository, RepositoryError
from agent_repo.seeds import seed_repository
from agent_repo.tools import AuthorityGrant, CallContext, Payload, ToolError, builtin_registry


class CLIError(ValueError):
    """A user-facing CLI input or configuration error."""


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        settings = Settings.from_env()
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {_safe_text(message, settings)}", file=sys.stderr)
        raise SystemExit(2)


def _add_graph_options(parser: argparse.ArgumentParser, *, root: bool = False) -> None:
    parser.add_argument(
        "--graph-file",
        type=Path,
        default=None if root else argparse.SUPPRESS,
        help="Persist the in-memory graph as JSON at this path; otherwise use Aura from .env when configured.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(prog="agent-repo", description="Versioned agent repository CLI.")
    _add_graph_options(parser, root=True)
    commands = parser.add_subparsers(dest="command", required=True)

    demo = commands.add_parser("demo", help="Run the ordered acceptance demonstration.")
    _add_graph_options(demo)

    ask = commands.add_parser("ask", help="Route and execute a query.")
    _add_graph_options(ask)
    ask.add_argument("query")
    ask.add_argument("--attachment", type=Path)

    explain = commands.add_parser("explain", help="Render the stored execution subgraph.")
    _add_graph_options(explain)
    explain.add_argument("execution_id")

    agents = commands.add_parser("agents", help="List agents or describe one.")
    _add_graph_options(agents)
    agents.add_argument("name", nargs="?")

    versions = commands.add_parser("versions", help="List an agent's versions and lineage.")
    _add_graph_options(versions)
    versions.add_argument("agent")
    versions.add_argument("--version", dest="version")
    versions.add_argument("--lineage", action="store_true")

    feedback = commands.add_parser("feedback", help="Record feedback for an execution.")
    _add_graph_options(feedback)
    feedback.add_argument("execution_id")
    feedback.add_argument("--user-id", required=True)
    feedback.add_argument("--rating", required=True, type=int, choices=(-1, 1))
    feedback.add_argument("--comment")

    case = commands.add_parser("case", help="Add or run automatic evaluation cases.")
    _add_graph_options(case)
    case_actions = case.add_subparsers(dest="case_action", required=True)
    case_add = case_actions.add_parser("add")
    _add_graph_options(case_add)
    case_add.add_argument("workflow_id")
    case_add.add_argument("--name", required=True)
    case_add.add_argument("--payload", required=True, help="Text payload to replay.")
    case_add.add_argument("--exact", action="append", default=[], metavar="FIELD=JSON_VALUE")
    case_add.add_argument("--must-not-contain", action="append", default=[])
    case_run = case_actions.add_parser("run")
    _add_graph_options(case_run)
    case_run.add_argument("workflow_id")

    proposals = commands.add_parser("proposals", help="List proposals or generate/evaluate a rewrite.")
    _add_graph_options(proposals)
    proposal_actions = proposals.add_subparsers(dest="proposal_action")
    proposal_list = proposal_actions.add_parser("list")
    _add_graph_options(proposal_list)
    generate = proposal_actions.add_parser("generate")
    _add_graph_options(generate)
    generate.add_argument("proposal_id")
    generate.add_argument("--rewrite-json", help="Supply a scripted JSON rewrite instead of the offline default.")
    generate.add_argument(
        "--llm",
        default=None,
        metavar="PROVIDER:MODEL",
        help="LLM provider:model spec to generate the candidate rewrite (default: offline mock).",
    )
    evaluate = proposal_actions.add_parser("evaluate")
    _add_graph_options(evaluate)
    evaluate.add_argument("proposal_id")

    approve = commands.add_parser("approve", help="Approve a proposal that passed evaluation.")
    _add_graph_options(approve)
    approve.add_argument("proposal_id")
    approve.add_argument("--by", required=True, help="Named human approver (naming convention, not authentication).")

    reject = commands.add_parser("reject", help="Reject an open proposal.")
    _add_graph_options(reject)
    reject.add_argument("proposal_id")
    reject.add_argument("--by", required=True)
    reject.add_argument("--reason", required=True)

    catalog = commands.add_parser("catalog", help="Print the generated agent catalog.")
    _add_graph_options(catalog)

    cypher = commands.add_parser("cypher", help="Export the graph as a Cypher script.")
    _add_graph_options(cypher)
    cypher.add_argument("--output", type=Path, help="Write the export to a file instead of stdout.")
    return parser


def _configured_secrets(settings: Settings) -> list[str]:
    secrets = [settings.hash_salt, settings.neo4j.password if settings.neo4j else ""]
    secret_names = ("PASSWORD", "SECRET", "TOKEN", "API_KEY", "HASH_SALT")
    configured = dotenv_values(Path(".env")) if Path(".env").is_file() else {}
    for source in (configured, os.environ):
        secrets.extend(
            str(value) for key, value in source.items()
            if value and any(marker in key.upper() for marker in secret_names)
            and key.upper() != "NEO4J_TEST_WIPE"
        )
    if settings.neo4j:
        secrets.extend((settings.neo4j.uri, settings.neo4j.user))
    return [secret for secret in secrets if secret]


def _redact_configured_secrets(text: str, settings: Settings) -> str:
    """Replace exact configured secret values; applies NO pattern-based scrubbing."""
    clean = text
    for secret in _configured_secrets(settings):
        clean = clean.replace(secret, "[REDACTED_CREDENTIAL]")
    return clean


def _safe_text(text: str, settings: Settings) -> str:
    clean = scrub(text)
    for secret in _configured_secrets(settings):
        clean = clean.replace(secret, "[REDACTED_CREDENTIAL]")
    return clean


def _make_store(settings: Settings, graph_file: Path | None) -> tuple[GraphStore, bool]:
    if graph_file is not None:
        path = graph_file.expanduser()
        return (InMemoryGraph.load(path) if path.is_file() else InMemoryGraph()), False
    if settings.neo4j is not None:
        return Neo4jGraph(settings.neo4j), True
    return InMemoryGraph(), False


def _print(text: str, settings: Settings, out: TextIO) -> None:
    print(_safe_text(text, settings), file=out)


def _feedback_loop(repo: AgentRepository, settings: Settings, llm: LLMProvider) -> FeedbackLoop:
    if not settings.hash_salt or len(settings.hash_salt) < 16:
        raise CLIError("Feedback requires AGENT_REPO_HASH_SALT of at least 16 characters in .env or the environment.")
    return FeedbackLoop(repo, UserIdHasher(settings.hash_salt), llm)


def _offline_llm() -> MockLLM:
    def draft_rewrite(_system_prompt: str, prompt: str) -> str:
        marker = "Current manifest: "
        raw = prompt.rsplit(marker, 1)[-1].splitlines()[0]
        manifest = AgentManifest.model_validate_json(raw)
        return json.dumps({
            "system_prompt": f"{manifest.system_prompt}\nCLI offline candidate rewrite.",
            "pipeline": [step.model_dump(mode="json") for step in manifest.pipeline],
        })

    return MockLLM(router_fn=draft_rewrite)


def _format_result(result: ExecutionResult) -> str:
    lines = [
        f"Status: {result.status}",
        f"Execution: {result.execution_id}",
    ]
    if result.plan is not None:
        lines.extend((result.plan.format_explanation(),))
        if result.plan.reused_workflow_id:
            lines.append(f"Reused workflow: {result.plan.reused_workflow_id}")
    if result.output is not None:
        lines.append(f"Output: {result.output.text or json.dumps(result.output.data or {}, sort_keys=True)}")
    if result.break_reason:
        lines.append(f"Breakpoint: {result.break_reason.value}")
        lines.append(result.user_message)
        if result.clarifying_question:
            lines.append(f"Clarifying question: {result.clarifying_question}")
    if result.proposal_id:
        lines.append(f"Proposal: {result.proposal_id}")
    if result.agent_request_id:
        lines.append(f"Draft agent request: {result.agent_request_id}")
    return "\n".join(lines)


def _case_checks(exact_values: Sequence[str], forbidden_values: Sequence[str]) -> list[CaseCheck]:
    checks: list[CaseCheck] = []
    for assignment in exact_values:
        field, separator, raw_value = assignment.partition("=")
        if not separator or not field.strip():
            raise CLIError(f"Invalid --exact {assignment!r}; expected FIELD=JSON_VALUE.")
        try:
            expected = json.loads(raw_value)
        except json.JSONDecodeError:
            expected = raw_value
        checks.append(CaseCheck(kind="exact_field", field=field.strip(), expected=expected))
    checks.extend(CaseCheck(kind="must_not_contain", value=value) for value in forbidden_values)
    if not checks:
        raise CLIError("Add at least one --exact or --must-not-contain check.")
    return checks


def _demo(repo: AgentRepository, settings: Settings, out: TextIO, llm: MockLLM) -> None:
    runner = Runner(repo)
    loop = _feedback_loop(repo, settings, llm)
    print("=== M5 acceptance demo (offline stubs are labelled) ===", file=out)

    def execute(label: str, query: str, *, payload: Payload | None = None,
                attachment: str | None = None, ctx: CallContext | None = None) -> ExecutionResult:
        print(f"\n[{label}]", file=out)
        result = runner.execute_query(query, payload=payload, attachment_path=attachment, ctx=ctx)
        _print(_format_result(result), settings, out)
        return result

    with tempfile.TemporaryDirectory(prefix="agent-repo-demo-") as temp_dir:
        temp = Path(temp_dir)
        audio_path = temp / "demo-audio.wav"
        audio_path.write_bytes(b"offline audio fixture")
        image_path = temp / "demo-image.png"
        image_path.write_bytes(b"offline image fixture")
        file_path = temp / "demo-report.txt"
        file_path.write_text("Quarterly report: 12 requests, 9 completed.", encoding="utf-8")

        text_query = "summarize the key points of this report: Alpha facts. Beta launch."
        execute("1. summarize text", text_query)
        execute("2. summarize audio file (transcriber is inserted)", "summarize the spoken meeting report",
                attachment=str(audio_path))
        execute("3. classify an image", "classify the objects in this image", attachment=str(image_path))
        execute("4. analyze a file", "analyze this report file", attachment=str(file_path))
        execute("5. generate an image", "generate an image of a blue landscape")

        email_query = "send an email with this summary to the team: Demo report ready."
        denied = execute("6a. HIGH-risk email denied without a grant", email_query)
        if denied.break_reason is None or denied.break_reason.value != "permission_denied":
            raise CLIError("Demo expected the HIGH-risk email call to be denied without authority.")
        grant = AuthorityGrant("email_send", "CLI Demo Human", "Explicit authorization for this demonstration.")
        execute("6b. HIGH-risk email allowed with an explicit grant", email_query,
                ctx=CallContext(actor="CLI Demo Human", grants=(grant,)))

        execute("7. no-match breakpoint and draft request",
                "triangulate quasar telemetry with a heliotropic lattice")
        execute("8a. missing attachment breakpoint", "classify this image")
        missing_file = temp / "does-not-exist.txt"
        execute("8b. missing file breakpoint", "analyze this report file", attachment=str(missing_file))

        reused = execute("9. same query type reused from workflow memory", text_query)
        if reused.workflow_id is None:
            raise CLIError("The text query did not create a workflow required by the feedback demo.")

        workflow_id = reused.workflow_id
        workflow = repo.graph.get_node(workflow_id)
        if workflow is None:
            raise CLIError("The remembered workflow disappeared during the demo.")
        goal_agent = str(workflow.props["goal_agent"])
        base_version = str(workflow.props["goal_version"])
        base = repo.get_version(goal_agent, base_version)
        if goal_agent != "text_summarizer":
            raise CLIError("The demo summary query did not route to text_summarizer.")
        pipeline = [step.model_dump(mode="json") for step in base.manifest.pipeline]
        summarize_step = next((step for step in pipeline if step.get("kind") == "tool"
                               and step.get("name") == "text_summarize"), None)
        if summarize_step is None:
            raise CLIError("The selected summarizer has no deterministic summary tool for the demo rewrite.")
        previous_count = int(summarize_step.get("params", {}).get("max_sentences", 2))
        target_count = 1 if previous_count != 1 else 2
        sample = Payload(modality=Modality.TEXT, text="Alpha facts. Beta launch. Gamma report.")
        summarize_step.setdefault("params", {})["max_sentences"] = target_count
        expected = repo.tools.get("text_summarize").fn(sample, dict(summarize_step["params"])).text
        loop.add_case(workflow_id, AutomaticCase(
            name=f"demo rewrite selects {target_count} summary sentence(s)",
            payload=sample,
            checks=[CaseCheck(kind="exact_field", field="text", expected=expected)],
        ))
        proposal_id: str | None = None
        for index in range(1, loop.distinct_user_threshold + 1):
            feedback_execution = runner.execute_query(text_query)
            submission = loop.record_feedback(
                feedback_execution.execution_id,
                f"cli-demo-user-{index}",
                -1,
                "The summary length needs adjustment.",
            )
            proposal_id = submission.proposal_id or proposal_id
        if proposal_id is None:
            raise CLIError("Distinct-user feedback did not create an improvement proposal.")
        _print("\n[10. three distinct users, scripted rewrite and evaluation]", settings, out)
        llm.queue_response(json.dumps({
            "system_prompt": "Summarize faithfully using the requested demo length.",
            "pipeline": pipeline,
        }))
        candidate_version = loop.generate_candidate(proposal_id)
        stored_proposal = repo.graph.get_node(proposal_id)
        _print(str(stored_proposal.props.get("rewrite_label", "")) if stored_proposal else "", settings, out)
        evaluation = loop.evaluate_proposal(proposal_id)
        _print(f"Candidate: {goal_agent}@{candidate_version}; evaluation: {evaluation.status}; "
               f"accuracy {evaluation.baseline_accuracy} -> {evaluation.candidate_accuracy}", settings, out)
        if evaluation.status != "pending_approval":
            raise CLIError("The scripted demo rewrite did not pass the evaluation gate.")

        new_workflow_id = loop.approve_proposal(proposal_id, "CLI Demo Human")
        next_result = execute("11. human approval and next query uses new version", text_query)
        if next_result.plan is None or next_result.plan.goal_version != candidate_version:
            raise CLIError("The next query did not use the human-approved workflow version.")
        lineage = repo.lineage(goal_agent, candidate_version)
        _print(f"Old version retained: {repo.get_version(goal_agent, base_version).version}; "
               f"lineage: {' <- '.join(f'{item.agent}@{item.version}' for item in lineage)}", settings, out)
        if not repo.graph.get_node(new_workflow_id):
            raise CLIError("The approved workflow was not stored.")

        approved_workflow = repo.graph.get_node(new_workflow_id)
        if approved_workflow is None:
            raise CLIError("The approved workflow is missing.")
        useless_proposal = ensure_open_proposal(
            repo, new_workflow_id, goal_agent, candidate_version, "demo_useless_rewrite",
            runner._clock().isoformat(),
        )
        current_step = next((dict(step) for step in pipeline
                             if step.get("kind") == "tool" and step.get("name") == "text_summarize"), None)
        if current_step is None:
            raise CLIError("Cannot construct the demo's gate-rejected rewrite.")
        current_expected = repo.tools.get("text_summarize").fn(
            sample, dict(current_step.get("params", {}))
        ).text
        loop.add_case(new_workflow_id, AutomaticCase(
            name="demo unchanged rewrite must not claim improvement",
            payload=sample,
            checks=[CaseCheck(kind="exact_field", field="text", expected=current_expected)],
        ))
        llm.queue_response(json.dumps({
            "system_prompt": base.manifest.system_prompt,
            "pipeline": pipeline,
        }))
        loop.generate_candidate(useless_proposal)
        useless_evaluation = loop.evaluate_proposal(useless_proposal)
        _print(f"Useless rewrite evaluation: {useless_evaluation.status} "
               "(expected rejected_by_gate)", settings, out)
        if useless_evaluation.status != "rejected_by_gate":
            raise CLIError("The demo's behavior-preserving rewrite unexpectedly passed the evaluation gate.")

        print("\n[12. LangGraph plan-approval pause]", file=out)
        engine = LangGraphEngine(runner, require_approval=True)
        pending = engine.start(query="summarize this text: LangGraph approval pause.")
        if not isinstance(pending, PendingApproval):
            raise CLIError("LangGraph did not pause for plan approval.")
        _print(f"Paused before execution; pending plan:\n{pending.plan.format_explanation()}", settings, out)
        resumed = engine.resume(pending, approved=True)
        _print(_format_result(resumed), settings, out)


def _dispatch(args: argparse.Namespace, repo: AgentRepository, settings: Settings,
              out: TextIO, llm: MockLLM) -> None:
    command = args.command
    if command == "demo":
        _demo(repo, settings, out, llm)
    elif command == "ask":
        result = Runner(repo).execute_query(args.query, attachment_path=str(args.attachment) if args.attachment else None)
        _print(_format_result(result), settings, out)
    elif command == "explain":
        subgraph = execution_subgraph(repo, args.execution_id)
        _print(render_execution_subgraph(subgraph), settings, out)
    elif command == "agents":
        names = [args.name] if args.name else repo.list_agents()
        for name in names:
            desc = repo.describe(name)
            _print(f"{desc.name}: {desc.purpose}\n  Useful for: {desc.usefulness}\n"
                   f"  Input: {', '.join(desc.input_modalities)} -> {desc.output_type}\n"
                   f"  Reachable tools: {', '.join(desc.reachable_tools) or 'none'}", settings, out)
    elif command == "versions":
        if args.lineage and not args.version:
            raise CLIError("--lineage requires --version.")
        records = repo.lineage(args.agent, args.version) if args.lineage and args.version else repo.versions(args.agent)
        default = repo.default_version(args.agent)
        for record in records:
            marker = " (default)" if default and default.version == record.version else ""
            _print(f"{record.agent}@{record.version}: {record.status.value}; author={record.author}{marker}", settings, out)
    elif command == "feedback":
        loop = _feedback_loop(repo, settings, llm)
        submission = loop.record_feedback(args.execution_id, args.user_id, args.rating, args.comment)
        _print(f"Feedback recorded: {submission.feedback_id}; proposal={submission.proposal_id or 'none'}", settings, out)
    elif command == "case":
        loop = _feedback_loop(repo, settings, llm)
        if args.case_action == "add":
            case = AutomaticCase(
                name=args.name,
                payload=Payload(modality=Modality.TEXT, text=args.payload),
                checks=_case_checks(args.exact, args.must_not_contain),
            )
            _print(f"Case recorded: {loop.add_case(args.workflow_id, case)}", settings, out)
        else:
            for result in loop.evaluate_cases(args.workflow_id):
                _print(f"{result.case_id}: {'passed' if result.passed else 'failed'}; "
                       f"elapsed={result.elapsed_seconds:.4f}s; tokens={result.tokens_used}; "
                       f"reasons={'; '.join(result.reasons) or 'none'}", settings, out)
    elif command == "proposals":
        action = args.proposal_action or "list"
        if action == "list":
            for node in sorted(repo.graph.find_nodes("Proposal"), key=lambda item: str(item.props.get("created_at", ""))):
                _print(f"{node.id}: {node.props.get('status')}; workflow={node.props.get('workflow_id')}; "
                       f"trigger={node.props.get('trigger')}", settings, out)
        else:
            active_llm: LLMProvider = llm
            if action == "generate" and getattr(args, "llm", None):
                active_llm = make_llm(args.llm)
                if isinstance(active_llm, MockLLM) and active_llm.router_fn is None:
                    active_llm.router_fn = llm.router_fn
            loop = _feedback_loop(repo, settings, active_llm)
            if action == "generate":
                if args.rewrite_json is not None:
                    if not isinstance(active_llm, MockLLM):
                        raise CLIError("--rewrite-json cannot be combined with a non-mock --llm provider.")
                    active_llm.queue_response(args.rewrite_json)
                _print(f"Candidate version: {loop.generate_candidate(args.proposal_id)}", settings, out)
            else:
                evaluation = loop.evaluate_proposal(args.proposal_id)
                _print(f"Evaluation: {evaluation.status}; accuracy "
                       f"{evaluation.baseline_accuracy} -> {evaluation.candidate_accuracy}; "
                       f"regressions={list(evaluation.regressions)}; latency_ok={evaluation.latency_ok}; "
                       f"token_budget_ok={evaluation.token_budget_ok}", settings, out)
    elif command == "approve":
        loop = _feedback_loop(repo, settings, llm)
        workflow_id = loop.approve_proposal(args.proposal_id, args.by)
        _print(f"Proposal approved; active workflow: {workflow_id}", settings, out)
    elif command == "reject":
        loop = _feedback_loop(repo, settings, llm)
        loop.reject_proposal(args.proposal_id, args.by, args.reason)
        _print(f"Proposal rejected: {args.proposal_id}", settings, out)
    elif command == "catalog":
        _print(repo.catalog_markdown(), settings, out)
    elif command == "cypher":
        script = _redact_configured_secrets(export_cypher(repo.graph), settings)
        if args.output:
            args.output.write_text(script, encoding="utf-8")
            _print(f"Cypher export written to {args.output}", settings, out)
        else:
            print(script, file=out)
    else:
        raise CLIError(f"Unknown command {command!r}.")


def main(
    argv: Sequence[str] | None = None,
    *,
    graph_store: GraphStore | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the CLI. `graph_store` is for embedding and tests; normal selection is via options/env."""
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = Settings()
    store: GraphStore | None = graph_store
    close_store = False
    try:
        settings = Settings.from_env()
        if store is None:
            store, close_store = _make_store(settings, args.graph_file)
        repo = AgentRepository(store, tools=builtin_registry(tool_settings=settings.tools))
        if not repo.list_agents():
            seed_repository(repo, "CLI Seed Maintainer")
        llm = _offline_llm()
        try:
            _dispatch(args, repo, settings, output, llm)
        finally:
            if graph_store is None and args.graph_file is not None and isinstance(store, InMemoryGraph):
                store.save(args.graph_file.expanduser())
        return 0
    except (CLIError, FeedbackError, RepositoryError, GraphError, NotHumanError, ValidationError,
            ValueError, OSError, KeyError, ToolError, LLMError) as exc:
        print(f"Error: {_safe_text(str(exc), settings)}", file=errors)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary redacts unexpected library errors.
        print(f"Error: {type(exc).__name__}: {_safe_text(str(exc), settings)}", file=errors)
        return 2
    finally:
        if close_store and isinstance(store, Neo4jGraph):
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
