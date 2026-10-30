# Evaluation

Evaluation decides whether a proposed agent rewrite may be offered for human approval. It runs the
stored automatic tests (`Case`s) against both the current and the candidate workflow. Implemented in
`agent_repo/feedback.py`.

## Automatic cases

A `Case` belongs to a workflow and contains:

- a `name`;
- a `payload` (the input to replay);
- one or more `checks`:
  - `exact_field` — a dotted field path (e.g. `data.kind` or `text`) must equal an expected JSON
    value;
  - `must_not_contain` — the case-insensitive output text (plus JSON data) must **not** contain a
    forbidden value;
- optional `latency_budget_seconds` and `token_budget` overrides.

Cases are added with `agent-repo case add WORKFLOW_ID --name … --payload … [--exact FIELD=JSON]
[--must-not-contain TEXT]` and run with `agent-repo case run WORKFLOW_ID`. Case JSON is scrubbed
before storage.

## Gate criteria

`evaluate_proposal()` compares the **baseline** workflow with the **candidate** (the candidate
version replaces the goal agent inside the same workflow) and passes only when **all** hold:

1. **Accuracy strictly improves** — `candidate_accuracy > baseline_accuracy`, where accuracy is the
   fraction of cases whose *checks* passed.
2. **No regression** — no case that passed on the baseline fails on the candidate.
3. **Latency budget holds** — every candidate case is within its latency budget (or the configured
   default `timeout_seconds`).
4. **Token budget holds** — every candidate case is within its token budget (or the configured
   default `token_budget`). Token use is a deterministic offline estimate of ~4 characters per
   token (`estimate_tokens()`, `agent_repo/execution/runner.py:163`), not real provider usage.

Accuracy is the objective; latency and cost are constraints. A pass sets the proposal to
`pending_approval`; otherwise it is `rejected_by_gate`.

## No cases means no promotion

If a workflow has **no cases**, `evaluate_proposal()` sets the proposal to `needs_cases` and returns
without evaluating. A proposal in `needs_cases` is never auto-promoted.

## Results recorded

Each evaluation stores `baseline_accuracy`, `candidate_accuracy`, the list of regressed `case_id`s,
the `latency_ok` / `token_budget_ok` flags and per-case results in the proposal's `evaluation_json`.
Each `Case` node also records its `last_passed`, `last_elapsed_seconds`, `last_tokens_used` and a
scrubbed `last_failure` after `case run`.

## Why this matters (mutation evidence)

[`MUTATION_CHECKS.md`](MUTATION_CHECKS.md) records the mutation-style evidence: removing or inverting
individual safety and gate rules makes specific tests fail, demonstrating that each rule is actually
load-bearing rather than merely asserted in prose. The dual-backend suite also asserts the same
behaviour on `InMemoryGraph` and on Neo4j AuraDB.
