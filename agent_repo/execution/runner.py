"""Shared execution policy, event-sourced trace, breakpoints, and runner (spec §6).

This module contains pure policy functions (`is_hard_stop`, `should_retry_tool`,
`check_step_limits`, `select_next_alternative`) so both the plain-Python orchestrator and
the LangGraph engine in Milestone 3 share identical retry, limit, and alternative-skipping
behaviour.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from agent_repo.config import ExecutionLimits
from agent_repo.manifest import Modality, Status
from agent_repo.privacy import scrub, scrub_obj
from agent_repo.repository import AgentRepository
from agent_repo.routing import (
    HARD_STOP_REASONS, BreakReason, Plan, PlanStep, Router, record_workflow_memory,
)
from agent_repo.tools import (
    CallContext, MissingInputError, Payload, PermissionDeniedError, ToolError,
)


class TraceEvent(BaseModel):
    """One immutable, scrubbed event in the execution trace (built from logged events, never LLM prose)."""

    model_config = ConfigDict(frozen=True)

    seq: int
    at: str
    event: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ExecutionTrace:
    """Append-only event log that scrubs every field on write."""

    def __init__(
        self,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        initial_events: list[TraceEvent] | tuple[TraceEvent, ...] = (),
    ) -> None:
        self._clock = clock
        self._events: list[TraceEvent] = list(initial_events)

    def log(self, event: str, **detail: Any) -> TraceEvent:
        scrubbed_detail = scrub_obj(detail)
        entry = TraceEvent(
            seq=len(self._events) + 1,
            at=self._clock().isoformat(),
            event=scrub(event),
            detail=scrubbed_detail if isinstance(scrubbed_detail, dict) else {"value": scrubbed_detail},
        )
        self._events.append(entry)
        return entry

    @property
    def events(self) -> tuple[TraceEvent, ...]:
        return tuple(self._events)

    def to_json(self) -> str:
        return json.dumps([e.model_dump(mode="json") for e in self._events], sort_keys=True)


# ---------------------------------------------------------------- SHARED POLICY FUNCTIONS

def is_hard_stop(reason: BreakReason) -> bool:
    """Hard-stop breakpoints never retry and never attempt alternative workflows."""
    return reason in HARD_STOP_REASONS


def should_retry_tool(attempt: int, limits: ExecutionLimits) -> bool:
    """`attempt` is 1-indexed (1 = first try). Retries up to `limits.max_retries_per_tool` times after the initial try."""
    return attempt <= limits.max_retries_per_tool


def check_limits_before_step(
    steps_taken: int,
    elapsed_seconds: float,
    tokens_used: int,
    limits: ExecutionLimits,
) -> BreakReason | None:
    if elapsed_seconds > limits.timeout_seconds:
        return BreakReason.TIMEOUT
    if tokens_used > limits.token_budget:
        return BreakReason.TOKEN_BUDGET
    if steps_taken >= limits.max_steps:
        return BreakReason.MAX_STEPS
    return None


def check_limits_after_step(
    elapsed_seconds: float,
    tokens_used: int,
    limits: ExecutionLimits,
) -> BreakReason | None:
    if elapsed_seconds > limits.timeout_seconds:
        return BreakReason.TIMEOUT
    if tokens_used > limits.token_budget:
        return BreakReason.TOKEN_BUDGET
    return None


def workflow_reachable_tool_names(repo: AgentRepository, steps: list[PlanStep]) -> set[str]:
    tools: set[str] = set()
    for step in steps:
        for rt in repo.reachable_tools(step.agent, step.version):
            tools.add(rt.tool)
    return tools


def select_next_alternative(
    repo: AgentRepository,
    alternatives: list[list[PlanStep]],
    exhausted_tools: set[str],
    trace: ExecutionTrace,
    limits: ExecutionLimits,
    alternatives_tried: int,
) -> tuple[list[PlanStep] | None, list[list[PlanStep]]]:
    """Pick the next alternative workflow.
    If an alternative would call a tool that already exhausted its retries, skip it and log `alternative_skipped`.
    """
    if alternatives_tried >= limits.max_alternative_workflows:
        return None, []
    remaining = list(alternatives)
    while remaining:
        candidate = remaining.pop(0)
        cand_tools = workflow_reachable_tool_names(repo, candidate)
        overlap = sorted(cand_tools & exhausted_tools)
        if overlap:
            trace.log(
                "alternative_skipped",
                goal_agent=candidate[-1].agent if candidate else "?",
                reason=f"Alternative workflow uses exhausted failing tool(s): {overlap}",
                exhausted_tools=overlap,
            )
            continue
        return candidate, remaining
    return None, []


def validate_step_output(payload: Payload | None, expected_modality: Modality) -> tuple[bool, str]:
    if payload is None:
        return False, "step returned None"
    if payload.modality is not expected_modality:
        return False, f"expected output modality {expected_modality.value}, got {payload.modality.value}"
    if expected_modality is Modality.TEXT and not (payload.text and payload.text.strip()):
        return False, "text output is empty"
    if expected_modality is Modality.JSON and not payload.data:
        return False, "json output data is empty"
    if expected_modality is Modality.IMAGE and not (payload.data or payload.path):
        return False, "image output has neither data nor path"
    return True, ""


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 10
    # Deterministic token estimate (~4 chars per token + base overhead).
    return max(10, len(text) // 4 + 12)


class DraftAgentGenerator(Protocol):
    """Callable/LLM interface that drafts a new agent specification when `no_match` occurs."""

    def draft_agent(self, query: str, payload_modality: Modality) -> dict[str, Any]: ...


class DefaultDraftGenerator:
    """Deterministic offline draft generator ( labeled MOCK / data-only draft )."""

    def draft_agent(self, query: str, payload_modality: Modality) -> dict[str, Any]:
        clean = scrub(query)
        return {
            "draft_only": True,
            "routable": False,
            "proposed_purpose": f"Handle request: {clean[:120]}",
            "input_modalities": [payload_modality.value],
            "output_type": Modality.TEXT.value,
            "note": "Stored as an AgentRequest draft only; never routable until a human implements and approves it.",
        }


@dataclass(frozen=True)
class ClarifyingMessage:
    what_went_wrong: str
    clarifying_question: str


def build_break_message(reason: BreakReason, detail: str) -> ClarifyingMessage:
    clean_detail = scrub(detail)
    questions: dict[BreakReason, str] = {
        BreakReason.NO_MATCH: "Could you rephrase the task or tell me which output format (summary, classification, file stats, image) you want?",
        BreakReason.MISSING_INPUT: "Could you attach the required file (audio, image, or document) and verify the file path exists?",
        BreakReason.PERMISSION_DENIED: "This action uses a HIGH-risk tool. Can a named human provide an explicit AuthorityGrant with a reason?",
        BreakReason.MAX_RETRIES: "A tool kept failing after all retries. Would you like to try a smaller input or check the tool dependency?",
        BreakReason.OUTPUT_INVALID: "An agent produced an invalid or empty output. Could you clarify the input format or expected fields?",
        BreakReason.MAX_STEPS: "The workflow exceeded the maximum step budget. Can we break this request into smaller steps?",
        BreakReason.TIMEOUT: "Execution exceeded the time limit. Would you like to retry with a shorter input or higher timeout?",
        BreakReason.TOKEN_BUDGET: "Execution exceeded the token budget. Can you provide a shorter excerpt to process?",
    }
    return ClarifyingMessage(
        what_went_wrong=f"Execution stopped at breakpoint '{reason.value}': {clean_detail}",
        clarifying_question=questions[reason],
    )


@dataclass
class ExecutionResult:
    execution_id: str
    status: str  # "succeeded" | "broken" | "declined"
    output: Payload | None
    plan: Plan | None
    workflow_id: str | None
    break_reason: BreakReason | None = None
    user_message: str = ""
    clarifying_question: str = ""
    escalation_id: str | None = None
    proposal_id: str | None = None
    agent_request_id: str | None = None
    trace: tuple[TraceEvent, ...] = ()
    steps_taken: int = 0
    tokens_used: int = 0
    elapsed_seconds: float = 0.0


class Runner:
    """Executes a routed plan step-by-step with shared policy, breakpoints, trace, and graph recording."""

    def __init__(
        self,
        repo: AgentRepository,
        router: Router | None = None,
        limits: ExecutionLimits | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        timer: Callable[[], float] | None = None,
        draft_generator: DraftAgentGenerator | None = None,
        token_estimator: Callable[[Payload, str], int] | None = None,
    ) -> None:
        self.repo = repo
        self.router = router or Router(repo)
        self.limits = limits or ExecutionLimits()
        self._clock = clock
        self._timer = timer
        self._draft_gen = draft_generator or DefaultDraftGenerator()
        self._token_estimator = token_estimator

    def _now_seconds(self) -> float:
        if self._timer is not None:
            return self._timer()
        return self._clock().timestamp()

    def _estimate_step_tokens(self, payload: Payload, step_name: str) -> int:
        if self._token_estimator is not None:
            return self._token_estimator(payload, step_name)
        return estimate_tokens(payload.text)

    def execute_query(
        self,
        query: str,
        payload: Payload | None = None,
        attachment_path: str | None = None,
        ctx: CallContext | None = None,
        approve_plan: Callable[[Plan], bool] | None = None,
    ) -> ExecutionResult:
        call_ctx = ctx or CallContext(actor="user")
        trace = ExecutionTrace(clock=self._clock)
        start_time = self._now_seconds()

        att_path = attachment_path or (payload.path if payload else None)
        decl_mod = payload.modality if payload is not None else None
        trace.log("request_received", query=query, attachment_path=att_path, actor=call_ctx.actor)

        route_out = self.router.route(
            query=query,
            attachment_path=att_path,
            declared_modality=decl_mod,
            max_alternatives=self.limits.max_alternative_workflows,
        )
        if route_out.break_reason is not None or route_out.plan is None:
            reason = route_out.break_reason or BreakReason.NO_MATCH
            trace.log("breakpoint_triggered", reason=reason.value, detail=route_out.detail, phase="plan")
            return self._finish_broken(
                query=query,
                facts_mod=route_out.facts.payload_modality if route_out.facts else (decl_mod or Modality.TEXT),
                plan=None,
                workflow_id=None,
                reason=reason,
                detail=route_out.detail,
                goal_agent_failed=False,
                trace=trace,
                steps_taken=0,
                tokens_used=0,
                elapsed_seconds=max(0.0, self._now_seconds() - start_time),
            )

        plan = route_out.plan
        trace.log(
            "plan_created",
            query_type_key=plan.query_type_key,
            goal_agent=f"{plan.goal_agent}@{plan.goal_version}",
            reused_workflow_id=plan.reused_workflow_id,
            steps=[f"{s.agent}@{s.version}" for s in plan.steps],
        )

        if approve_plan is not None and not approve_plan(plan):
            trace.log("plan_declined", by=call_ctx.actor)
            now_iso = self._clock().isoformat()
            exec_node = self.repo.graph.create_node("Execution", {
                "query": scrub(query),
                "query_type_key": plan.query_type_key,
                "status": "declined",
                "plan_text": scrub(plan.format_explanation()),
                "plan_json": scrub(plan.model_dump_json()),
                "trace_json": trace.to_json(),
                "output_text": "",
                "break_reason": "",
                "steps_taken": 0,
                "tokens_used": 0,
                "elapsed_seconds": round(max(0.0, self._now_seconds() - start_time), 4),
                "created_at": now_iso,
            })
            return ExecutionResult(
                execution_id=exec_node.id,
                status="declined",
                output=None,
                plan=plan,
                workflow_id=plan.reused_workflow_id,
                user_message="Execution declined by user at plan approval.",
                trace=trace.events,
            )

        initial_payload = payload or Payload(
            modality=plan.payload_modality,
            text=query if plan.payload_modality is Modality.TEXT else None,
            path=att_path,
        )
        return self._attempt_workflows(
            query=query,
            plan=plan,
            initial_payload=initial_payload,
            ctx=call_ctx,
            trace=trace,
            start_time=start_time,
        )

    def _attempt_workflows(
        self,
        query: str,
        plan: Plan,
        initial_payload: Payload,
        ctx: CallContext,
        trace: ExecutionTrace,
        start_time: float,
    ) -> ExecutionResult:
        current_steps = list(plan.steps)
        remaining_alts = list(plan.alternatives)
        exhausted_tools: set[str] = set()
        alternatives_tried = 0
        steps_taken = 0
        tokens_used = 0
        last_reason: BreakReason = BreakReason.MAX_RETRIES
        last_detail = ""
        goal_agent_failed = False

        while True:
            ok, out_payload, brk_reason, brk_detail, steps_taken, tokens_used, failed_role = self._run_steps(
                steps=current_steps,
                initial_payload=initial_payload,
                ctx=ctx,
                trace=trace,
                start_time=start_time,
                steps_taken=steps_taken,
                tokens_used=tokens_used,
                exhausted_tools=exhausted_tools,
            )
            if ok and out_payload is not None:
                now_iso = self._clock().isoformat()
                active_plan = Plan(
                    query_type_key=plan.query_type_key,
                    payload_modality=plan.payload_modality,
                    intent_tokens=plan.intent_tokens,
                    dropped_modality_words=plan.dropped_modality_words,
                    goal_agent=current_steps[-1].agent,
                    goal_version=current_steps[-1].version,
                    reused_workflow_id=plan.reused_workflow_id if current_steps == plan.steps else None,
                    steps=current_steps,
                    alternatives=[],
                )
                wf_id = record_workflow_memory(self.repo, active_plan, now_iso)
                trace.log("execution_succeeded", workflow_id=wf_id, output_modality=out_payload.modality.value)
                elapsed = round(max(0.0, self._now_seconds() - start_time), 4)
                exec_node = self._record_execution_node(
                    query=query,
                    plan=active_plan,
                    workflow_id=wf_id,
                    status="succeeded",
                    output=out_payload,
                    break_reason=None,
                    trace=trace,
                    steps_taken=steps_taken,
                    tokens_used=tokens_used,
                    elapsed_seconds=elapsed,
                )
                return ExecutionResult(
                    execution_id=exec_node,
                    status="succeeded",
                    output=out_payload,
                    plan=active_plan,
                    workflow_id=wf_id,
                    trace=trace.events,
                    steps_taken=steps_taken,
                    tokens_used=tokens_used,
                    elapsed_seconds=elapsed,
                )

            assert brk_reason is not None
            last_reason = brk_reason
            last_detail = brk_detail
            if failed_role == "goal":
                goal_agent_failed = True

            if is_hard_stop(brk_reason):
                break

            next_steps, remaining_alts = select_next_alternative(
                repo=self.repo,
                alternatives=remaining_alts,
                exhausted_tools=exhausted_tools,
                trace=trace,
                limits=self.limits,
                alternatives_tried=alternatives_tried,
            )
            if next_steps is None:
                break
            alternatives_tried += 1
            trace.log(
                "alternative_workflow_started",
                attempt=alternatives_tried,
                goal_agent=f"{next_steps[-1].agent}@{next_steps[-1].version}",
            )
            current_steps = next_steps

        elapsed = round(max(0.0, self._now_seconds() - start_time), 4)
        return self._finish_broken(
            query=query,
            facts_mod=plan.payload_modality,
            plan=plan,
            workflow_id=plan.reused_workflow_id,
            reason=last_reason,
            detail=last_detail,
            goal_agent_failed=goal_agent_failed,
            trace=trace,
            steps_taken=steps_taken,
            tokens_used=tokens_used,
            elapsed_seconds=elapsed,
        )

    def _run_steps(
        self,
        steps: list[PlanStep],
        initial_payload: Payload,
        ctx: CallContext,
        trace: ExecutionTrace,
        start_time: float,
        steps_taken: int,
        tokens_used: int,
        exhausted_tools: set[str],
    ) -> tuple[bool, Payload | None, BreakReason | None, str, int, int, str]:
        cur = initial_payload
        for plan_step in steps:
            ok, cur, brk, detail, steps_taken, tokens_used = self._run_agent_version(
                agent=plan_step.agent,
                version=plan_step.version,
                role=plan_step.role,
                payload=cur,
                ctx=ctx,
                trace=trace,
                start_time=start_time,
                steps_taken=steps_taken,
                tokens_used=tokens_used,
                exhausted_tools=exhausted_tools,
            )
            if not ok:
                return False, None, brk, detail, steps_taken, tokens_used, plan_step.role
            assert cur is not None
        return True, cur, None, "", steps_taken, tokens_used, ""

    def _run_agent_version(
        self,
        agent: str,
        version: str,
        role: str,
        payload: Payload,
        ctx: CallContext,
        trace: ExecutionTrace,
        start_time: float,
        steps_taken: int,
        tokens_used: int,
        exhausted_tools: set[str],
    ) -> tuple[bool, Payload | None, BreakReason | None, str, int, int]:
        rec = self.repo.get_version(agent, version)
        trace.log("agent_started", agent=agent, version=version, role=role, input_modality=payload.modality.value)
        cur = payload

        for pipe_step in rec.manifest.pipeline:
            elapsed = max(0.0, self._now_seconds() - start_time)
            pre_brk = check_limits_before_step(steps_taken, elapsed, tokens_used, self.limits)
            if pre_brk is not None:
                detail = f"Limit {pre_brk.value} exceeded before step '{pipe_step.name}' (steps={steps_taken}, elapsed={elapsed:.2f}s, tokens={tokens_used})."
                trace.log("breakpoint_triggered", reason=pre_brk.value, detail=detail, agent=agent, step=pipe_step.name)
                return False, None, pre_brk, detail, steps_taken, tokens_used

            if pipe_step.kind == "agent":
                ok, sub_out, brk, detail, steps_taken, tokens_used = self._run_agent_version(
                    agent=pipe_step.name,
                    version=pipe_step.version or "0.1",
                    role=role,
                    payload=cur,
                    ctx=ctx,
                    trace=trace,
                    start_time=start_time,
                    steps_taken=steps_taken,
                    tokens_used=tokens_used,
                    exhausted_tools=exhausted_tools,
                )
                if not ok:
                    return False, None, brk, detail, steps_taken, tokens_used
                assert sub_out is not None
                cur = sub_out
                continue

            # Tool call with retry loop governed by shared policy.
            steps_taken += 1
            attempt = 0
            while True:
                attempt += 1
                try:
                    trace.log("tool_call", tool=pipe_step.name, agent=agent, attempt=attempt)
                    cur = self.repo.tools.call(pipe_step.name, cur, dict(pipe_step.params), ctx)
                    tokens_used += self._estimate_step_tokens(cur, pipe_step.name)
                    trace.log("tool_ok", tool=pipe_step.name, attempt=attempt, tokens_used=tokens_used)
                    break
                except PermissionDeniedError as exc:
                    trace.log("breakpoint_triggered", reason=BreakReason.PERMISSION_DENIED.value, tool=pipe_step.name, detail=str(exc))
                    return False, None, BreakReason.PERMISSION_DENIED, str(exc), steps_taken, tokens_used
                except MissingInputError as exc:
                    trace.log("breakpoint_triggered", reason=BreakReason.MISSING_INPUT.value, tool=pipe_step.name, detail=str(exc))
                    return False, None, BreakReason.MISSING_INPUT, str(exc), steps_taken, tokens_used
                except ToolError as exc:
                    trace.log("tool_error", tool=pipe_step.name, attempt=attempt, error=str(exc))
                    if should_retry_tool(attempt, self.limits):
                        continue
                    exhausted_tools.add(pipe_step.name)
                    detail = f"Tool '{pipe_step.name}' failed after {attempt} attempt(s): {exc}"
                    trace.log("breakpoint_triggered", reason=BreakReason.MAX_RETRIES.value, tool=pipe_step.name, detail=detail)
                    return False, None, BreakReason.MAX_RETRIES, detail, steps_taken, tokens_used

            elapsed = max(0.0, self._now_seconds() - start_time)
            post_brk = check_limits_after_step(elapsed, tokens_used, self.limits)
            if post_brk is not None:
                detail = f"Limit {post_brk.value} exceeded after tool '{pipe_step.name}' (elapsed={elapsed:.2f}s, tokens={tokens_used})."
                trace.log("breakpoint_triggered", reason=post_brk.value, detail=detail, agent=agent, step=pipe_step.name)
                return False, None, post_brk, detail, steps_taken, tokens_used

        valid, invalid_reason = validate_step_output(cur, rec.manifest.output_type)
        if not valid:
            detail = f"Agent {agent}@{version} produced invalid output: {invalid_reason}"
            trace.log("breakpoint_triggered", reason=BreakReason.OUTPUT_INVALID.value, agent=agent, detail=detail)
            return False, None, BreakReason.OUTPUT_INVALID, detail, steps_taken, tokens_used

        trace.log("agent_completed", agent=agent, version=version, output_modality=cur.modality.value)
        return True, cur, None, "", steps_taken, tokens_used

    def _record_execution_node(
        self,
        query: str,
        plan: Plan | None,
        workflow_id: str | None,
        status: str,
        output: Payload | None,
        break_reason: BreakReason | None,
        trace: ExecutionTrace,
        steps_taken: int,
        tokens_used: int,
        elapsed_seconds: float,
    ) -> str:
        now_iso = self._clock().isoformat()
        q_key = plan.query_type_key if plan else "unmatched"
        exec_node = self.repo.graph.create_node("Execution", {
            "query": scrub(query),
            "query_type_key": q_key,
            "status": status,
            "plan_text": scrub(plan.format_explanation()) if plan else "",
            "plan_json": scrub(plan.model_dump_json()) if plan else "",
            "trace_json": trace.to_json(),
            "output_text": scrub(output.text or "") if output else "",
            "break_reason": break_reason.value if break_reason else "",
            "steps_taken": steps_taken,
            "tokens_used": tokens_used,
            "elapsed_seconds": elapsed_seconds,
            "created_at": now_iso,
        })
        qt_nodes = self.repo.graph.find_nodes("QueryType", {"key": q_key})
        if qt_nodes:
            self.repo.graph.create_rel("OF_TYPE", exec_node.id, qt_nodes[0].id)
        if workflow_id is not None:
            self.repo.graph.create_rel("RAN", exec_node.id, workflow_id)
        return exec_node.id

    def _finish_broken(
        self,
        query: str,
        facts_mod: Modality,
        plan: Plan | None,
        workflow_id: str | None,
        reason: BreakReason,
        detail: str,
        goal_agent_failed: bool,
        trace: ExecutionTrace,
        steps_taken: int,
        tokens_used: int,
        elapsed_seconds: float,
    ) -> ExecutionResult:
        now_iso = self._clock().isoformat()
        msg = build_break_message(reason, detail)
        exec_id = self._record_execution_node(
            query=query,
            plan=plan,
            workflow_id=workflow_id,
            status="broken",
            output=None,
            break_reason=reason,
            trace=trace,
            steps_taken=steps_taken,
            tokens_used=tokens_used,
            elapsed_seconds=elapsed_seconds,
        )
        # File an Escalation node on every final break.
        esc_node = self.repo.graph.create_node("Escalation", {
            "execution_id": exec_id,
            "reason": reason.value,
            "detail": scrub(detail),
            "clarifying_question": msg.clarifying_question,
            "created_at": now_iso,
        })
        self.repo.graph.create_rel("ESCALATED", exec_id, esc_node.id)

        proposal_id: str | None = None
        if goal_agent_failed and plan is not None:
            target_wf_id = workflow_id
            if target_wf_id is None:
                from agent_repo.feedback import record_failed_workflow

                target_wf_id = record_failed_workflow(self.repo, plan, now_iso)
                self.repo.graph.create_rel("RAN", exec_id, target_wf_id)
            from agent_repo.feedback import ensure_open_proposal

            proposal_id = ensure_open_proposal(
                self.repo,
                target_wf_id,
                plan.goal_agent,
                plan.goal_version,
                f"loop_break:{reason.value}",
                now_iso,
            )

        agent_request_id: str | None = None
        if reason is BreakReason.NO_MATCH:
            draft = scrub_obj(self._draft_gen.draft_agent(query, facts_mod))
            req_node = self.repo.graph.create_node("AgentRequest", {
                "query": scrub(query),
                "payload_modality": facts_mod.value,
                "status": "draft",
                "routable": False,
                "draft_json": json.dumps(draft, sort_keys=True),
                "created_at": now_iso,
            })
            self.repo.graph.create_rel("REQUESTED", exec_id, req_node.id)
            agent_request_id = req_node.id

        return ExecutionResult(
            execution_id=exec_id,
            status="broken",
            output=None,
            plan=plan,
            workflow_id=workflow_id,
            break_reason=reason,
            user_message=msg.what_went_wrong,
            clarifying_question=msg.clarifying_question,
            escalation_id=esc_node.id,
            proposal_id=proposal_id,
            agent_request_id=agent_request_id,
            trace=trace.events,
            steps_taken=steps_taken,
            tokens_used=tokens_used,
            elapsed_seconds=elapsed_seconds,
        )
