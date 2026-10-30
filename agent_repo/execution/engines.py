"""Plain-Python autonomous orchestrator (`PlainEngine`) and LangGraph state-graph orchestrator
(`LangGraphEngine`) with shared node transitions and shared retry/limit/breakpoint policy (spec §6, §9 M3).

Both engines execute the exact same state machine:
  `plan -> approval -> attempt -> learn`
with `attempt` looping back to `attempt` for the next alternative workflow, and exits to
`finish_broken` and `finish_declined`.

When `require_approval=True` and no synchronous `approve_plan` callback is provided,
`LangGraphEngine` uses a LangGraph checkpoint interrupt (`interrupt_before=["approval"]`)
so the caller can inspect `pending.plan` and then call `resume(pending, approved=True/False)`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from agent_repo.execution.runner import (
    ExecutionResult, ExecutionTrace, Runner, TraceEvent, is_hard_stop, select_next_alternative,
)
from agent_repo.manifest import Modality, Status
from agent_repo.privacy import scrub
from agent_repo.routing import BreakReason, Plan, PlanStep, record_workflow_memory
from agent_repo.tools import CallContext, Payload


class EngineState(TypedDict, total=False):
    query: str
    payload: Payload | None
    attachment_path: str | None
    ctx: CallContext
    start_time: float
    trace_events: list[TraceEvent]
    facts_modality: Modality
    plan: Plan | None
    approved: bool | None
    current_steps: list[PlanStep]
    remaining_alternatives: list[list[PlanStep]]
    exhausted_tools: list[str]
    alternatives_tried: int
    steps_taken: int
    tokens_used: int
    output: Payload | None
    break_reason: BreakReason | None
    break_detail: str
    goal_agent_failed: bool
    next_route: str
    result: ExecutionResult | None


def _trace_from_state(runner: Runner, state: EngineState) -> ExecutionTrace:
    return ExecutionTrace(clock=runner._clock, initial_events=state.get("trace_events", []))  # noqa: SLF001


# ---------------------------------------------------------------- SHARED NODE FUNCTIONS

def node_plan(runner: Runner, state: EngineState) -> dict[str, Any]:
    query = state["query"]
    payload = state.get("payload")
    att_path = state.get("attachment_path") or (payload.path if payload else None)
    decl_mod = payload.modality if payload is not None else None
    ctx = state["ctx"]
    trace = _trace_from_state(runner, state)

    trace.log("request_received", query=query, attachment_path=att_path, actor=ctx.actor)
    route_out = runner.router.route(
        query=query,
        attachment_path=att_path,
        declared_modality=decl_mod,
        max_alternatives=runner.limits.max_alternative_workflows,
    )
    facts_mod = route_out.facts.payload_modality if route_out.facts else (decl_mod or Modality.TEXT)
    if route_out.break_reason is not None or route_out.plan is None:
        reason = route_out.break_reason or BreakReason.NO_MATCH
        trace.log("breakpoint_triggered", reason=reason.value, detail=route_out.detail, phase="plan")
        return {
            "trace_events": list(trace.events),
            "facts_modality": facts_mod,
            "plan": None,
            "break_reason": reason,
            "break_detail": route_out.detail,
            "next_route": "finish_broken",
        }

    plan = route_out.plan
    trace.log(
        "plan_created",
        query_type_key=plan.query_type_key,
        goal_agent=f"{plan.goal_agent}@{plan.goal_version}",
        reused_workflow_id=plan.reused_workflow_id,
        steps=[f"{s.agent}@{s.version}" for s in plan.steps],
    )
    return {
        "trace_events": list(trace.events),
        "facts_modality": facts_mod,
        "plan": plan,
        "current_steps": list(plan.steps),
        "remaining_alternatives": list(plan.alternatives),
        "exhausted_tools": [],
        "alternatives_tried": 0,
        "steps_taken": 0,
        "tokens_used": 0,
        "goal_agent_failed": False,
        "next_route": "approval",
    }


def node_approval(
    runner: Runner,
    state: EngineState,
    approve_plan: Callable[[Plan], bool] | None,
) -> dict[str, Any]:
    plan = state.get("plan")
    assert plan is not None
    trace = _trace_from_state(runner, state)
    decision = state.get("approved")
    if decision is None:
        decision = approve_plan(plan) if approve_plan is not None else True
    if not decision:
        trace.log("plan_declined", by=state["ctx"].actor)
        return {"trace_events": list(trace.events), "approved": False, "next_route": "finish_declined"}
    # The plan is a snapshot; approval/resume must revalidate every pinned version.
    for step in plan.steps:
        try:
            rec = runner.repo.verify_version_integrity(step.agent, step.version)
        except Exception as exc:  # noqa: BLE001 - convert stale plans into a safe breakpoint
            trace.log("breakpoint_triggered", reason=BreakReason.PERMISSION_DENIED.value, detail=str(exc))
            return {
                "trace_events": list(trace.events),
                "break_reason": BreakReason.PERMISSION_DENIED,
                "break_detail": str(exc),
                "next_route": "finish_broken",
            }
        if rec.status is not Status.STABLE:
            detail = f"Pinned agent {step.agent}@{step.version} is no longer stable"
            trace.log("breakpoint_triggered", reason=BreakReason.PERMISSION_DENIED.value, detail=detail)
            return {
                "trace_events": list(trace.events),
                "break_reason": BreakReason.PERMISSION_DENIED,
                "break_detail": detail,
                "next_route": "finish_broken",
            }
    return {"trace_events": list(trace.events), "approved": True, "next_route": "attempt"}


def node_attempt(runner: Runner, state: EngineState) -> dict[str, Any]:
    plan = state["plan"]
    assert plan is not None
    payload = state.get("payload")
    att_path = state.get("attachment_path") or (payload.path if payload else None)
    initial_payload = payload or Payload(
        modality=plan.payload_modality,
        text=state["query"] if plan.payload_modality is Modality.TEXT else None,
        path=att_path,
    )
    current_steps = list(state["current_steps"])
    remaining_alts = list(state["remaining_alternatives"])
    exhausted_tools = set(state["exhausted_tools"])
    alternatives_tried = int(state["alternatives_tried"])
    steps_taken = int(state["steps_taken"])
    tokens_used = int(state["tokens_used"])
    goal_agent_failed = bool(state["goal_agent_failed"])
    trace = _trace_from_state(runner, state)

    ok, out_payload, brk_reason, brk_detail, steps_taken, tokens_used, failed_role = runner._run_steps(  # noqa: SLF001
        steps=current_steps,
        initial_payload=initial_payload,
        ctx=state["ctx"],
        trace=trace,
        start_time=state["start_time"],
        steps_taken=steps_taken,
        tokens_used=tokens_used,
        exhausted_tools=exhausted_tools,
    )
    if ok and out_payload is not None:
        return {
            "trace_events": list(trace.events),
            "output": out_payload,
            "current_steps": current_steps,
            "exhausted_tools": sorted(exhausted_tools),
            "steps_taken": steps_taken,
            "tokens_used": tokens_used,
            "next_route": "learn",
        }

    assert brk_reason is not None
    if failed_role == "goal":
        goal_agent_failed = True

    if not is_hard_stop(brk_reason):
        next_steps, remaining_alts = select_next_alternative(
            repo=runner.repo,
            alternatives=remaining_alts,
            exhausted_tools=exhausted_tools,
            trace=trace,
            limits=runner.limits,
            alternatives_tried=alternatives_tried,
        )
        if next_steps is not None:
            alternatives_tried += 1
            trace.log(
                "alternative_workflow_started",
                attempt=alternatives_tried,
                goal_agent=f"{next_steps[-1].agent}@{next_steps[-1].version}",
            )
            return {
                "trace_events": list(trace.events),
                "current_steps": next_steps,
                "remaining_alternatives": remaining_alts,
                "exhausted_tools": sorted(exhausted_tools),
                "alternatives_tried": alternatives_tried,
                "steps_taken": steps_taken,
                "tokens_used": tokens_used,
                "break_reason": brk_reason,
                "break_detail": brk_detail,
                "goal_agent_failed": goal_agent_failed,
                "next_route": "attempt",
            }

    return {
        "trace_events": list(trace.events),
        "remaining_alternatives": remaining_alts,
        "exhausted_tools": sorted(exhausted_tools),
        "alternatives_tried": alternatives_tried,
        "steps_taken": steps_taken,
        "tokens_used": tokens_used,
        "break_reason": brk_reason,
        "break_detail": brk_detail,
        "goal_agent_failed": goal_agent_failed,
        "next_route": "finish_broken",
    }


def node_learn(runner: Runner, state: EngineState) -> dict[str, Any]:
    plan = state["plan"]
    out_payload = state["output"]
    assert plan is not None and out_payload is not None
    current_steps = state["current_steps"]
    trace = _trace_from_state(runner, state)
    now_iso = runner._clock().isoformat()  # noqa: SLF001

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
    wf_id = record_workflow_memory(runner.repo, active_plan, now_iso)
    trace.log("execution_succeeded", workflow_id=wf_id, output_modality=out_payload.modality.value)
    elapsed = round(max(0.0, runner._now_seconds() - state["start_time"]), 4)  # noqa: SLF001
    exec_id = runner._record_execution_node(  # noqa: SLF001
        query=state["query"],
        plan=active_plan,
        workflow_id=wf_id,
        status="succeeded",
        output=out_payload,
        break_reason=None,
        trace=trace,
        steps_taken=state["steps_taken"],
        tokens_used=state["tokens_used"],
        elapsed_seconds=elapsed,
        actor=state.get("actor", "user"),
    )
    return {
        "trace_events": list(trace.events),
        "result": ExecutionResult(
            execution_id=exec_id,
            status="succeeded",
            output=out_payload,
            plan=active_plan,
            workflow_id=wf_id,
            trace=trace.events,
            steps_taken=state["steps_taken"],
            tokens_used=state["tokens_used"],
            elapsed_seconds=elapsed,
        ),
    }


def node_finish_broken(runner: Runner, state: EngineState) -> dict[str, Any]:
    elapsed = round(max(0.0, runner._now_seconds() - state["start_time"]), 4)  # noqa: SLF001
    plan = state.get("plan")
    reason = state.get("break_reason") or BreakReason.NO_MATCH
    trace = _trace_from_state(runner, state)
    res = runner._finish_broken(  # noqa: SLF001
        query=state["query"],
        facts_mod=state.get("facts_modality", Modality.TEXT),
        plan=plan,
        workflow_id=plan.reused_workflow_id if plan else None,
        reason=reason,
        detail=state.get("break_detail", ""),
        goal_agent_failed=bool(state.get("goal_agent_failed", False)),
        trace=trace,
        steps_taken=int(state.get("steps_taken", 0)),
        tokens_used=int(state.get("tokens_used", 0)),
        elapsed_seconds=elapsed,
    )
    return {"trace_events": list(trace.events), "result": res}


def node_finish_declined(runner: Runner, state: EngineState) -> dict[str, Any]:
    plan = state["plan"]
    assert plan is not None
    trace = _trace_from_state(runner, state)
    now_iso = runner._clock().isoformat()  # noqa: SLF001
    elapsed = round(max(0.0, runner._now_seconds() - state["start_time"]), 4)  # noqa: SLF001
    exec_node = runner.repo.graph.create_node("Execution", {
        "query": scrub(state["query"]),
        "query_type_key": plan.query_type_key,
        "status": "declined",
        "plan_text": scrub(plan.format_explanation()),
        "plan_json": scrub(plan.model_dump_json()),
        "trace_json": trace.to_json(),
        "output_text": "",
        "break_reason": "",
        "steps_taken": 0,
        "tokens_used": 0,
        "elapsed_seconds": elapsed,
        "created_at": now_iso,
    })
    return {
        "trace_events": list(trace.events),
        "result": ExecutionResult(
            execution_id=exec_node.id,
            status="declined",
            output=None,
            plan=plan,
            workflow_id=plan.reused_workflow_id,
            user_message="Execution declined by user at plan approval.",
            trace=trace.events,
        ),
    }


# ---------------------------------------------------------------- PLAIN PYTHON ENGINE

class PlainEngine:
    """Plain-Python state-machine orchestrator sharing `node_*` and `policy` with `LangGraphEngine`."""

    def __init__(self, runner: Runner) -> None:
        self.runner = runner

    def run(
        self,
        query: str,
        payload: Payload | None = None,
        attachment_path: str | None = None,
        ctx: CallContext | None = None,
        approve_plan: Callable[[Plan], bool] | None = None,
    ) -> ExecutionResult:
        state: EngineState = {
            "query": query,
            "payload": payload,
            "attachment_path": attachment_path,
            "ctx": ctx or CallContext(actor="user"),
            "start_time": self.runner._now_seconds(),  # noqa: SLF001
            "trace_events": [],
            "approved": None,
        }
        state.update(node_plan(self.runner, state))
        if state["next_route"] == "finish_broken":
            state.update(node_finish_broken(self.runner, state))
            assert state["result"] is not None
            return state["result"]

        state.update(node_approval(self.runner, state, approve_plan))
        if state["next_route"] == "finish_declined":
            state.update(node_finish_declined(self.runner, state))
            assert state["result"] is not None
            return state["result"]

        while state["next_route"] == "attempt":
            state.update(node_attempt(self.runner, state))

        if state["next_route"] == "learn":
            state.update(node_learn(self.runner, state))
        else:
            state.update(node_finish_broken(self.runner, state))

        assert state["result"] is not None
        return state["result"]


# ---------------------------------------------------------------- LANGGRAPH ENGINE

@dataclass(frozen=True)
class PendingApproval:
    """Returned by `LangGraphEngine.start()` when `require_approval=True` pauses before `approval`."""
    thread_id: str
    plan: Plan


class LangGraphEngine:
    """LangGraph `StateGraph` orchestrator (`plan -> approval -> attempt -> learn`, with exits to
    `finish_broken` and `finish_declined` and optional `interrupt_before=['approval']`).
    """

    def __init__(self, runner: Runner, require_approval: bool = False) -> None:
        self.runner = runner
        self.require_approval = require_approval
        self._approve_cb: Callable[[Plan], bool] | None = None
        self._checkpointer = MemorySaver()
        self._thread_seq = 0
        self._terminal_results: dict[str, ExecutionResult] = {}
        self._compiled = self._build_graph(interrupt=require_approval)

    def _build_graph(self, interrupt: bool) -> Any:
        builder: StateGraph[EngineState] = StateGraph(EngineState)
        builder.add_node("plan", lambda s: node_plan(self.runner, s))
        builder.add_node("approval", lambda s: node_approval(self.runner, s, self._approve_cb))
        builder.add_node("attempt", lambda s: node_attempt(self.runner, s))
        builder.add_node("learn", lambda s: node_learn(self.runner, s))
        builder.add_node("finish_broken", lambda s: node_finish_broken(self.runner, s))
        builder.add_node("finish_declined", lambda s: node_finish_declined(self.runner, s))

        builder.set_entry_point("plan")

        def _after_plan(s: EngineState) -> Literal["approval", "finish_broken"]:
            return "approval" if s.get("next_route") == "approval" else "finish_broken"

        def _after_approval(s: EngineState) -> Literal["attempt", "finish_declined"]:
            return "attempt" if s.get("next_route") == "attempt" else "finish_declined"

        def _after_attempt(s: EngineState) -> Literal["attempt", "learn", "finish_broken"]:
            route = s.get("next_route")
            if route == "attempt":
                return "attempt"
            if route == "learn":
                return "learn"
            return "finish_broken"

        builder.add_conditional_edges(
            "plan", _after_plan, {"approval": "approval", "finish_broken": "finish_broken"}
        )
        builder.add_conditional_edges(
            "approval", _after_approval, {"attempt": "attempt", "finish_declined": "finish_declined"}
        )
        builder.add_conditional_edges(
            "attempt",
            _after_attempt,
            {"attempt": "attempt", "learn": "learn", "finish_broken": "finish_broken"},
        )
        builder.add_edge("learn", END)
        builder.add_edge("finish_broken", END)
        builder.add_edge("finish_declined", END)

        return builder.compile(
            checkpointer=self._checkpointer,
            interrupt_before=["approval"] if interrupt else None,
        )

    def _next_config(self) -> tuple[str, dict[str, Any]]:
        self._thread_seq += 1
        tid = f"thread-{self._thread_seq}"
        return tid, {"configurable": {"thread_id": tid}}

    def run(
        self,
        query: str,
        payload: Payload | None = None,
        attachment_path: str | None = None,
        ctx: CallContext | None = None,
        approve_plan: Callable[[Plan], bool] | None = None,
    ) -> ExecutionResult:
        """Run end-to-end. If `require_approval=True` and `approve_plan` is given, pauses at the
        LangGraph interrupt, evaluates `approve_plan(pending.plan)`, and resumes automatically.
        """
        self._approve_cb = approve_plan
        started = self.start(query=query, payload=payload, attachment_path=attachment_path, ctx=ctx)
        if isinstance(started, ExecutionResult):
            return started
        decision = approve_plan(started.plan) if approve_plan is not None else True
        return self.resume(started, approved=decision)

    def start(
        self,
        query: str,
        payload: Payload | None = None,
        attachment_path: str | None = None,
        ctx: CallContext | None = None,
    ) -> ExecutionResult | PendingApproval:
        """Start execution. In `require_approval=True` mode, pauses before `approval` using a
        LangGraph interrupt and returns `PendingApproval(thread_id, plan)` so the caller can show
        the plan to a human before anything runs.
        """
        tid, config = self._next_config()
        initial: EngineState = {
            "query": query,
            "payload": payload,
            "attachment_path": attachment_path,
            "ctx": ctx or CallContext(actor="user"),
            "start_time": self.runner._now_seconds(),  # noqa: SLF001
            "trace_events": [],
            "approved": None,
        }
        out: EngineState = self._compiled.invoke(initial, config=config)
        if out.get("result") is not None:
            return out["result"]  # type: ignore[return-value]
        plan = out.get("plan")
        assert plan is not None
        return PendingApproval(thread_id=tid, plan=plan)

    def resume(self, pending: PendingApproval, approved: bool) -> ExecutionResult:
        """Resume a paused `PendingApproval` thread after the human approves or declines the plan."""
        cached = self._terminal_results.get(pending.thread_id)
        if cached is not None:
            return cached
        config = {"configurable": {"thread_id": pending.thread_id}}
        self._compiled.update_state(config, {"approved": approved})
        out: EngineState = self._compiled.invoke(None, config=config)
        assert out.get("result") is not None
        result = out["result"]  # type: ignore[assignment]
        self._terminal_results[pending.thread_id] = result
        return result

    def mermaid_diagram(self) -> str:
        """Export the LangGraph StateGraph as a Mermaid flowchart string (spec §6, §9 M3)."""
        return str(self._compiled.get_graph().draw_mermaid())
