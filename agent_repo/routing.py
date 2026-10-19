"""Autonomous routing from facts, pluggable embedder, prerequisite chain insertion, and workflow memory.

Rules enforced here (spec §6 + binding change #5):
- Payload modality comes strictly from the attached file (extension/MIME data table) or
  explicit attachment modality, defaulting to `text` when nothing is attached.
- Modality-word rule: a modality word (e.g. "image", "audio", "file") is dropped from the
  intent tokens ONLY when the payload is of that same modality. In "generate an image of a
  report" (text payload), "image" stays so `image_generator` matches.
- Goal agent is chosen ONLY from agent metadata scores (`purpose`, `usefulness`,
  `example_queries` via the pluggable `Embedder`). There is NO intent-to-output-type table
  and no `if "pdf" in query` style rules anywhere.
- Prerequisite chain insertion uses BFS over `chainable` stable agents (matching output_type
  to required input_modality), and refuses any prerequisite that can transitively reach a
  HIGH-risk tool (`repo.reaches_high_risk`).
- Workflow memory (`QueryType` -> `PREFERS` -> `Workflow`): stores a newly composed workflow
  on its first successful run, and reuses it on future requests ONLY when the new request's
  scored goal agent is the same as the stored workflow's goal agent (overlapping words alone
  never trigger reuse).
"""

from __future__ import annotations

import json
import math
import re
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from agent_repo.config import RouterConfig
from agent_repo.manifest import Modality, Status
from agent_repo.privacy import scrub
from agent_repo.repository import AgentRepository, AgentVersionRecord


class BreakReason(StrEnum):
    NO_MATCH = "no_match"
    MISSING_INPUT = "missing_input"
    PERMISSION_DENIED = "permission_denied"
    MAX_RETRIES = "max_retries"
    OUTPUT_INVALID = "output_invalid"
    MAX_STEPS = "max_steps"
    TIMEOUT = "timeout"
    TOKEN_BUDGET = "token_budget"


HARD_STOP_REASONS: frozenset[BreakReason] = frozenset({
    BreakReason.PERMISSION_DENIED,
    BreakReason.MISSING_INPUT,
    BreakReason.TIMEOUT,
    BreakReason.TOKEN_BUDGET,
    BreakReason.NO_MATCH,
})


# Data table mapping file extension / MIME prefix to payload Modality (facts of what was attached).
ATTACHMENT_EXTENSION_MODALITIES: Mapping[str, Modality] = {
    ".wav": Modality.AUDIO, ".mp3": Modality.AUDIO, ".m4a": Modality.AUDIO,
    ".ogg": Modality.AUDIO, ".flac": Modality.AUDIO,
    ".png": Modality.IMAGE, ".jpg": Modality.IMAGE, ".jpeg": Modality.IMAGE,
    ".gif": Modality.IMAGE, ".webp": Modality.IMAGE, ".bmp": Modality.IMAGE, ".svg": Modality.IMAGE,
    ".csv": Modality.FILE, ".tsv": Modality.FILE, ".txt": Modality.FILE,
    ".pdf": Modality.FILE, ".json": Modality.FILE, ".md": Modality.FILE, ".log": Modality.FILE,
}

# Vocabulary associated with each modality, used ONLY to drop redundant modality words
# when the attached payload is already of that modality.
MODALITY_VOCABULARY: Mapping[Modality, frozenset[str]] = {
    Modality.AUDIO: frozenset({"audio", "recording", "voice", "sound", "mp3", "wav"}),
    Modality.IMAGE: frozenset({"image", "picture", "photo", "photograph", "png", "jpg", "jpeg"}),
    Modality.FILE: frozenset({"file", "csv", "dataset", "spreadsheet"}),
    Modality.TEXT: frozenset({"text", "passage", "paragraph"}),
    Modality.JSON: frozenset({"json"}),
}

_STOPWORDS = frozenset(
    "a an and are as at be by can could do for from give has have how i in into is it its me my "
    "of on or our please some that the their them then this to up us want was we what with would you your".split()
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _stem(word: str) -> str:
    """Lightweight deterministic suffix stemmer so inflections ('summarize'/'summarizer', 'classify'/'classification') share a root."""
    if len(word) <= 4:
        return word
    for suffix, repl in (
        ("ization", "ize"), ("isation", "ize"), ("izing", "ize"), ("ising", "ize"),
        ("izer", "ize"), ("iser", "ize"), ("izes", "ize"), ("ises", "ize"),
        ("ication", "ify"), ("ifying", "ify"), ("ifier", "ify"), ("ifies", "ify"),
        ("cribing", "cribe"), ("criber", "cribe"), ("cribes", "cribe"), ("cription", "cribe"),
        ("cript", "cribe"), ("generating", "generate"), ("generation", "generate"),
        ("generator", "generate"), ("analyzing", "analyze"), ("analyzer", "analyze"),
        ("analysis", "analyze"), ("summarizing", "summarize"), ("summarizer", "summarize"),
        ("summary", "summarize"), ("summaries", "summarize"),
        ("classified", "classify"), ("classifier", "classify"), ("classification", "classify"),
        ("transcribed", "transcribe"), ("transcriber", "transcribe"), ("transcription", "transcribe"),
        ("transcript", "transcribe"), ("recordings", "recording"), ("recorded", "recording"),
        ("pictures", "picture"), ("images", "image"), ("photos", "photo"),
        ("files", "file"), ("documents", "document"), ("notes", "note"), ("minutes", "minute"),
        ("meetings", "meeting"), ("emails", "email"), ("messages", "message"),
        ("illustrations", "illustration"), ("statistics", "statistic"), ("stats", "statistic"),
    ):
        if word.endswith(suffix) and len(word) - len(suffix) + len(repl) >= 3:
            return word[: -len(suffix)] + repl
    if word.endswith("ing") and len(word) > 5:
        return word[:-3]
    if word.endswith("ed") and len(word) > 4:
        return word[:-2]
    if word.endswith("es") and len(word) > 4:
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    raw = _TOKEN_RE.findall(text.lower())
    return [_stem(w) for w in raw if w not in _STOPWORDS and len(w) > 1]


class Embedder(Protocol):
    """Pluggable text matcher (offline bag-of-words by default; dense embeddings can implement this)."""

    def similarity(self, query_tokens: list[str], document_text: str) -> tuple[float, list[str]]:
        """Return (score in [0, 1], sorted list of matched query terms)."""


class BagOfWordsEmbedder:
    """Deterministic offline bag-of-words cosine/overlap matcher."""

    def similarity(self, query_tokens: list[str], document_text: str) -> tuple[float, list[str]]:
        if not query_tokens:
            return 0.0, []
        doc_tokens = set(tokenize(document_text))
        if not doc_tokens:
            return 0.0, []
        q_set = set(query_tokens)
        matched = sorted(q_set & doc_tokens)
        if not matched:
            return 0.0, []
        # Asymmetric overlap + cosine blend so concise queries matching a long manifest score well.
        query_coverage = len(matched) / len(q_set)
        cosine = len(matched) / math.sqrt(len(q_set) * len(doc_tokens))
        return round(0.65 * query_coverage + 0.35 * cosine, 4), matched


@dataclass(frozen=True)
class RequestFacts:
    """Facts extracted from the user's request before any routing decision."""
    raw_query: str
    scrubbed_query: str
    payload_modality: Modality
    attachment_path: str | None
    has_attachment: bool
    intent_tokens: tuple[str, ...]
    dropped_modality_words: tuple[str, ...]


class PlanStep(BaseModel):
    """One explainable step in a routed workflow plan (spec §6)."""

    model_config = ConfigDict(frozen=True)

    order: int
    role: str  # "prerequisite" | "goal"
    agent: str
    version: str
    purpose: str
    usefulness: str
    reason: str
    how_it_helps: str
    matched_terms: list[str] = Field(default_factory=list)
    score: float = 0.0
    input_modality: Modality
    output_modality: Modality
    tools: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    """Complete explainable execution plan, plus alternative workflows for fallback."""

    model_config = ConfigDict(frozen=True)

    query_type_key: str
    payload_modality: Modality
    intent_tokens: list[str]
    dropped_modality_words: list[str] = Field(default_factory=list)
    goal_agent: str
    goal_version: str
    reused_workflow_id: str | None = None
    steps: list[PlanStep]
    alternatives: list[list[PlanStep]] = Field(default_factory=list)

    def format_explanation(self) -> str:
        header = [
            f"Query type: {self.query_type_key} (payload={self.payload_modality.value}, goal={self.goal_agent}@{self.goal_version})",
        ]
        if self.dropped_modality_words:
            header.append(f"Dropped payload-redundant modality words: {', '.join(self.dropped_modality_words)}")
        if self.reused_workflow_id:
            header.append(f"Workflow memory: reused active workflow {self.reused_workflow_id}")
        lines = header + ["Plan steps:"]
        for s in self.steps:
            tools_str = ", ".join(s.tools) if s.tools else "none"
            lines.append(
                f"  {s.order + 1}. [{s.role}] {s.agent}@{s.version} "
                f"({s.input_modality.value} -> {s.output_modality.value})\n"
                f"     What it does: {s.purpose}\n"
                f"     Why chosen:   {s.reason}\n"
                f"     How it helps: {s.how_it_helps}\n"
                f"     Tools:        {tools_str}"
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class RouteOutcome:
    plan: Plan | None
    break_reason: BreakReason | None = None
    detail: str = ""
    facts: RequestFacts | None = None


def infer_payload_modality(
    attachment_path: str | None = None,
    declared_modality: Modality | None = None,
    mime_type: str | None = None,
) -> Modality:
    """Determine payload modality from the attached artifact's facts (never from query words)."""
    if declared_modality is not None:
        return declared_modality
    if mime_type:
        low = mime_type.lower()
        if low.startswith("audio/"):
            return Modality.AUDIO
        if low.startswith("image/"):
            return Modality.IMAGE
        if low.startswith("text/csv") or low.startswith("application/"):
            return Modality.FILE
    if attachment_path:
        ext = Path(attachment_path).suffix.lower()
        return ATTACHMENT_EXTENSION_MODALITIES.get(ext, Modality.FILE)
    return Modality.TEXT


def extract_facts(
    query: str,
    attachment_path: str | None = None,
    declared_modality: Modality | None = None,
    mime_type: str | None = None,
) -> RequestFacts:
    payload_mod = infer_payload_modality(attachment_path, declared_modality, mime_type)
    has_att = bool(attachment_path) or (declared_modality is not None and declared_modality is not Modality.TEXT)
    raw_words = _TOKEN_RE.findall(query.lower())
    vocab_to_drop = MODALITY_VOCABULARY.get(payload_mod, frozenset()) if has_att else frozenset()
    dropped: list[str] = []
    kept_words: list[str] = []
    for w in raw_words:
        if w in vocab_to_drop or _stem(w) in vocab_to_drop:
            dropped.append(w)
        else:
            kept_words.append(w)
    tokens = [_stem(w) for w in kept_words if w not in _STOPWORDS and len(w) > 1]
    if not tokens:
        # If the user's query only contained the modality word itself, fall back to full tokens.
        tokens = tokenize(query)
        dropped = []
    return RequestFacts(
        raw_query=query,
        scrubbed_query=scrub(query),
        payload_modality=payload_mod,
        attachment_path=attachment_path,
        has_attachment=has_att,
        intent_tokens=tuple(tokens),
        dropped_modality_words=tuple(sorted(set(dropped))),
    )


class Router:
    """Autonomous fact-based router with BFS chain insertion and workflow memory."""

    def __init__(
        self,
        repo: AgentRepository,
        config: RouterConfig | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self.repo = repo
        self.config = config or RouterConfig()
        self.embedder = embedder or BagOfWordsEmbedder()

    def _candidate_versions(self) -> list[AgentVersionRecord]:
        """Return the active/default stable version for each agent in the repository."""
        out: list[AgentVersionRecord] = []
        for name in self.repo.list_agents():
            rec = self.repo.default_version(name)
            if rec is not None and rec.status is Status.STABLE:
                out.append(rec)
        return out

    def score_agent(self, facts: RequestFacts, rec: AgentVersionRecord) -> tuple[float, list[str]]:
        m = rec.manifest
        q = list(facts.intent_tokens)
        s_p, m_p = self.embedder.similarity(q, m.purpose)
        s_u, m_u = self.embedder.similarity(q, m.usefulness)
        s_e, m_e = self.embedder.similarity(q, " ".join(m.example_queries))
        w_sum = self.config.purpose_weight + self.config.usefulness_weight + self.config.examples_weight
        weighted = (
            self.config.purpose_weight * s_p
            + self.config.usefulness_weight * s_u
            + self.config.examples_weight * s_e
        ) / (w_sum if w_sum > 0 else 1.0)
        best_single = max(s_p, s_u, s_e)
        score = round(0.55 * best_single + 0.45 * weighted, 4)
        matched = sorted(set(m_p) | set(m_u) | set(m_e))
        return score, matched

    def _find_prerequisite_chain(
        self, start_modality: Modality, target_modalities: list[Modality]
    ) -> list[AgentVersionRecord] | None:
        """BFS over `chainable` stable agents that do NOT transitively reach a HIGH-risk tool."""
        if start_modality in target_modalities:
            return []
        chainable = [
            r for r in self._candidate_versions()
            if r.manifest.chainable and not self.repo.reaches_high_risk(r.agent, r.version)
        ]
        queue: deque[tuple[Modality, list[AgentVersionRecord]]] = deque([(start_modality, [])])
        visited: set[Modality] = {start_modality}
        while queue:
            current_mod, path = queue.popleft()
            for cand in chainable:
                if current_mod not in cand.manifest.input_modalities:
                    continue
                out_mod = cand.manifest.output_type
                new_path = [*path, cand]
                if out_mod in target_modalities:
                    return new_path
                if out_mod not in visited:
                    visited.add(out_mod)
                    queue.append((out_mod, new_path))
        return None

    def _build_steps(
        self,
        facts: RequestFacts,
        prereqs: list[AgentVersionRecord],
        goal: AgentVersionRecord,
        score: float,
        matched: list[str],
    ) -> list[PlanStep]:
        steps: list[PlanStep] = []
        cur_mod = facts.payload_modality
        for idx, pre in enumerate(prereqs):
            tools = [
                f"{t.tool} ({t.risk.value}{', STUB' if t.is_stub else ''}) via {t.via}"
                for t in self.repo.reachable_tools(pre.agent, pre.version)
            ]
            steps.append(PlanStep(
                order=idx,
                role="prerequisite",
                agent=pre.agent,
                version=pre.version,
                purpose=pre.manifest.purpose,
                usefulness=pre.manifest.usefulness,
                reason=(
                    f"Inserted automatically as a chainable prerequisite because the attached payload is "
                    f"'{cur_mod.value}' and goal agent '{goal.agent}' requires "
                    f"{'/'.join(m.value for m in goal.manifest.input_modalities)}."
                ),
                how_it_helps=f"{pre.manifest.usefulness} Converts '{cur_mod.value}' into '{pre.manifest.output_type.value}'.",
                matched_terms=[],
                score=1.0,
                input_modality=cur_mod,
                output_modality=pre.manifest.output_type,
                tools=tools,
            ))
            cur_mod = pre.manifest.output_type

        goal_tools = [
            f"{t.tool} ({t.risk.value}{', STUB' if t.is_stub else ''}) via {t.via}"
            for t in self.repo.reachable_tools(goal.agent, goal.version)
        ]
        in_mod = cur_mod if cur_mod in goal.manifest.input_modalities else goal.manifest.input_modalities[0]
        steps.append(PlanStep(
            order=len(steps),
            role="goal",
            agent=goal.agent,
            version=goal.version,
            purpose=goal.manifest.purpose,
            usefulness=goal.manifest.usefulness,
            reason=(
                f"Matched request intent terms {matched} against stored metadata (score={score:.2f})."
            ),
            how_it_helps=goal.manifest.usefulness,
            matched_terms=matched,
            score=score,
            input_modality=in_mod,
            output_modality=goal.manifest.output_type,
            tools=goal_tools,
        ))
        return steps

    @staticmethod
    def query_type_key(payload_modality: Modality, goal_agent: str) -> str:
        return f"{payload_modality.value}->{goal_agent}"

    def find_reusable_workflow(self, facts: RequestFacts, scored_goal_agent: str) -> tuple[str, list[PlanStep]] | None:
        """Look up QueryType -> PREFERS -> Workflow and return it ONLY if its goal agent matches `scored_goal_agent`."""
        q_key = self.query_type_key(facts.payload_modality, scored_goal_agent)
        qt_nodes = self.repo.graph.find_nodes("QueryType", {"key": q_key})
        if not qt_nodes:
            return None
        qt = qt_nodes[0]
        pref_rels = [
            rel for rel in self.repo.graph.rels(start=qt.id, rel_type="PREFERS")
            if rel.props.get("active", True)
        ]
        if not pref_rels:
            return None
        wf_node = self.repo.graph.get_node(pref_rels[-1].end)
        if wf_node is None:
            return None
        if not wf_node.props.get("active", True):
            return None
        # Verify the stored workflow's goal agent is identical to the newly scored goal agent.
        if wf_node.props.get("goal_agent") != scored_goal_agent:
            return None
        inc_rels = sorted(
            self.repo.graph.rels(start=wf_node.id, rel_type="INCLUDES"),
            key=lambda r: int(r.props.get("order", 0)),
        )
        if not inc_rels:
            return None
        steps: list[PlanStep] = []
        cur_mod = facts.payload_modality
        for rel in inc_rels:
            ver_node = self.repo.graph.get_node(rel.end)
            if ver_node is None:
                return None
            rec = self.repo.get_version(ver_node.props["agent"], ver_node.props["version"])
            if rec.status is not Status.STABLE:
                return None
            tools = [
                f"{t.tool} ({t.risk.value}{', STUB' if t.is_stub else ''}) via {t.via}"
                for t in self.repo.reachable_tools(rec.agent, rec.version)
            ]
            in_mod = cur_mod if cur_mod in rec.manifest.input_modalities else rec.manifest.input_modalities[0]
            steps.append(PlanStep(
                order=int(rel.props.get("order", len(steps))),
                role=str(rel.props.get("role", "goal")),
                agent=rec.agent,
                version=rec.version,
                purpose=rec.manifest.purpose,
                usefulness=rec.manifest.usefulness,
                reason=str(rel.props.get("reason", "Reused from active workflow memory.")),
                how_it_helps=rec.manifest.usefulness,
                matched_terms=[],
                score=1.0,
                input_modality=in_mod,
                output_modality=rec.manifest.output_type,
                tools=tools,
            ))
            cur_mod = rec.manifest.output_type
        return wf_node.id, steps

    def route(
        self,
        query: str,
        attachment_path: str | None = None,
        declared_modality: Modality | None = None,
        mime_type: str | None = None,
        max_alternatives: int = 1,
    ) -> RouteOutcome:
        facts = extract_facts(query, attachment_path, declared_modality, mime_type)
        scored: list[tuple[float, list[str], AgentVersionRecord]] = []
        for rec in self._candidate_versions():
            score, matched = self.score_agent(facts, rec)
            if score >= self.config.min_score_threshold and matched:
                scored.append((score, matched, rec))
        # Sort by score descending; tie-break by whether input modality matches payload directly, then agent name.
        scored.sort(key=lambda item: (
            -item[0],
            0 if facts.payload_modality in item[2].manifest.input_modalities else 1,
            item[2].agent,
        ))
        if not scored:
            return RouteOutcome(
                plan=None,
                break_reason=BreakReason.NO_MATCH,
                detail=f"No repository agent matched query intent tokens {list(facts.intent_tokens)}.",
                facts=facts,
            )

        # Evaluate top candidate (and any alternative workflows).
        primary_score, primary_matched, primary_goal = scored[0]
        q_key = self.query_type_key(facts.payload_modality, primary_goal.agent)

        # Check if primary goal needs an attachment that wasn't provided and has no chain from text.
        reused = self.find_reusable_workflow(facts, primary_goal.agent)
        if reused is not None:
            wf_id, primary_steps = reused
            first_needed = primary_steps[0].input_modality
            if first_needed is not Modality.TEXT and not facts.has_attachment:
                return RouteOutcome(
                    plan=None,
                    break_reason=BreakReason.MISSING_INPUT,
                    detail=f"Agent '{primary_goal.agent}' requires a '{first_needed.value}' attachment, but none was provided.",
                    facts=facts,
                )
            if facts.attachment_path and not Path(facts.attachment_path).is_file():
                return RouteOutcome(
                    plan=None,
                    break_reason=BreakReason.MISSING_INPUT,
                    detail=f"Attached file not found: {Path(facts.attachment_path).name}",
                    facts=facts,
                )
            goal_step = primary_steps[-1]
            return RouteOutcome(
                plan=Plan(
                    query_type_key=q_key,
                    payload_modality=facts.payload_modality,
                    intent_tokens=list(facts.intent_tokens),
                    dropped_modality_words=list(facts.dropped_modality_words),
                    goal_agent=goal_step.agent,
                    goal_version=goal_step.version,
                    reused_workflow_id=wf_id,
                    steps=primary_steps,
                    alternatives=[],
                ),
                facts=facts,
            )

        # Compose workflow from facts + BFS prerequisite chain for the highest-scoring goal agent
        # (and any alternative workflows that share the top goal intent score within a small tie margin).
        top_score = scored[0][0]
        top_candidates = [item for item in scored if top_score - item[0] <= 0.15]

        valid_plans: list[tuple[AgentVersionRecord, list[PlanStep]]] = []
        missing_input_detail: str | None = None

        for idx, (score, matched, cand) in enumerate(top_candidates):
            chain = self._find_prerequisite_chain(facts.payload_modality, cand.manifest.input_modalities)
            if chain is None:
                if idx == 0:
                    needed = "/".join(m.value for m in cand.manifest.input_modalities)
                    return RouteOutcome(
                        plan=None,
                        break_reason=BreakReason.MISSING_INPUT,
                        detail=(
                            f"Goal agent '{cand.agent}' requires input modality '{needed}', "
                            f"got '{facts.payload_modality.value}', and no safe chainable prerequisite converts between them."
                        ),
                        facts=facts,
                    )
                continue
            steps = self._build_steps(facts, chain, cand, score, matched)
            first_in = steps[0].input_modality
            if first_in is not Modality.TEXT and not facts.has_attachment:
                if idx == 0:
                    return RouteOutcome(
                        plan=None,
                        break_reason=BreakReason.MISSING_INPUT,
                        detail=(
                            f"Goal agent '{cand.agent}' requires a '{first_in.value}' attachment, but none was provided."
                        ),
                        facts=facts,
                    )
                continue
            if facts.attachment_path and not Path(facts.attachment_path).is_file():
                return RouteOutcome(
                    plan=None,
                    break_reason=BreakReason.MISSING_INPUT,
                    detail=f"Attached file not found: {Path(facts.attachment_path).name}",
                    facts=facts,
                )
            valid_plans.append((cand, steps))

        if not valid_plans:
            return RouteOutcome(
                plan=None,
                break_reason=BreakReason.MISSING_INPUT,
                detail=missing_input_detail or "Required input modality is missing.",
                facts=facts,
            )

        chosen_goal, chosen_steps = valid_plans[0]
        alt_steps = [steps for _, steps in valid_plans[1: 1 + max_alternatives]]
        return RouteOutcome(
            plan=Plan(
                query_type_key=self.query_type_key(facts.payload_modality, chosen_goal.agent),
                payload_modality=facts.payload_modality,
                intent_tokens=list(facts.intent_tokens),
                dropped_modality_words=list(facts.dropped_modality_words),
                goal_agent=chosen_goal.agent,
                goal_version=chosen_goal.version,
                reused_workflow_id=None,
                steps=chosen_steps,
                alternatives=alt_steps,
            ),
            facts=facts,
        )


def record_workflow_memory(repo: AgentRepository, plan: Plan, created_at: str) -> str:
    """Store a newly composed workflow as the QueryType's active (`PREFERS`) workflow on first success."""
    if plan.reused_workflow_id:
        return plan.reused_workflow_id
    qt_nodes = repo.graph.find_nodes("QueryType", {"key": plan.query_type_key})
    if qt_nodes:
        qt = qt_nodes[0]
    else:
        qt = repo.graph.create_node("QueryType", {
            "key": plan.query_type_key,
            "payload_modality": plan.payload_modality.value,
            "goal_agent": plan.goal_agent,
            "created_at": created_at,
        })
    # If QueryType already has an active workflow, keep it (first success wins until human-approved upgrade).
    existing_prefs = [
        rel for rel in repo.graph.rels(start=qt.id, rel_type="PREFERS")
        if rel.props.get("active", True)
    ]
    if existing_prefs:
        return existing_prefs[-1].end

    wf = repo.graph.create_node("Workflow", {
        "query_type_key": plan.query_type_key,
        "goal_agent": plan.goal_agent,
        "goal_version": plan.goal_version,
        "steps_json": json.dumps([s.model_dump(mode="json") for s in plan.steps], sort_keys=True),
        "created_at": created_at,
        "active": True,
    })
    for step in plan.steps:
        ver = repo.get_version(step.agent, step.version)
        repo.graph.create_rel("INCLUDES", wf.id, ver.node_id, {
            "order": step.order,
            "role": step.role,
            "reason": scrub(step.reason),
        })
    repo.graph.create_rel("PREFERS", qt.id, wf.id, {"since": created_at, "active": True})
    return wf.id
