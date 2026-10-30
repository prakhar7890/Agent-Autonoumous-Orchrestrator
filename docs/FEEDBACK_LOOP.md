# Feedback and the Improvement Loop

The loop turns dissatisfaction into a reviewed, testable improvement — never an automatic one.
Everything below is implemented in `agent_repo/feedback.py` and covered by tests.

## 1. Feedback validation

- An **identified user** is required; the user id is immediately replaced by a salted-HMAC-SHA256
  hash (`UserIdHasher`). Raw ids are never stored.
- `rating` must be exactly `+1` or `-1`.
- **One rating per user per execution** (duplicate `GOT` edges by the same hash are refused).
- **Hourly rate limit** per hashed user (default 10 submissions / rolling hour; configurable).
- A **negative** rating requires a **substantive comment**: at least 8 characters, not a known
  placeholder ("n/a", "none", …), and containing at least one alphanumeric character.
- Comments are scrubbed with `scrub()` before storage.

## 2. Triggers

A `Proposal` opens when any of these happens **and** the workflow has no open proposal already:

- **N distinct users** (default 3) report a problem on the **same workflow** (distinct **hashed**
  user ids with a `-1` rating).
- An **automatic case fails** during `evaluate_cases()`.
- The **goal agent hits a loop break** during execution (opened from the runner).

Exactly **one open proposal per workflow** at a time (`ensure_open_proposal`); the open statuses are
`open`, `needs_cases`, `pending_approval`.

## 3. Untrusted LLM rewrite

`generate_candidate()` asks the LLM for a JSON object containing **exactly** the keys
`system_prompt` and `pipeline`, and creates a `candidate` version. Guardrails (in the repository,
never only in the prompt):

- Invalid JSON, wrong key set, wrong value types, or any changed forbidden field is rejected, and the
  error is recorded on the proposal (`rewrite_error`).
- An LLM (`llm:*` author) may not create an agent's first version, may not change input/output
  modalities, and may not introduce a HIGH-risk tool **directly or through a sub-agent**.
- The rewrite label is stored: `MOCK LLM: scripted rewrite` for the deterministic offline provider,
  otherwise `<provider>:<model>`. **All LLM output is treated as untrusted input.**

## 4. Evaluation gate

`evaluate_proposal()` runs every stored `Case` in the workflow on the **baseline** pipeline and on
the **candidate** pipeline (substituting the candidate version for its goal agent), then decides:

- Accuracy must **strictly improve** (`candidate_accuracy > baseline_accuracy`).
- **No previously passing case may regress.**
- **Latency and token budgets must hold** (per-case budgets, or the configured defaults).
- **No cases ⇒ `needs_cases`**, and the proposal can never be auto-promoted.

A pass sets the proposal to `pending_approval`; otherwise it is `rejected_by_gate`. See
[`EVALUATION.md`](EVALUATION.md).

## 5. Human approval, workflow switch, rollback

- `approve_proposal()` requires a **named human** (`require_human`), and only a proposal in
  `pending_approval` can be approved.
- Approval submits and promotes the candidate to `stable`, builds a new `Workflow` that uses the
  candidate version, and **re-points** the query type's active workflow to it (the previous `PREFERS`
  edge and workflow are marked inactive, never deleted).
- Approval **does not change the agent's `default_version`** — the improvement applies to the query
  **type**, not globally. The code verifies this explicitly.
- `rollback()` re-points the query type to a prior workflow; it also requires a named human.
- `reject_proposal()` records the rejection reason and, if a candidate version exists, rejects it
  too.

## Demo evidence

The offline demo (`agent-repo demo`) shows the whole loop deterministically: three distinct users
report a problem, a `MOCK LLM: scripted rewrite` candidate passes the gate
(`accuracy 0.0 -> 1.0` in the run recorded for M6), a human approves, the next query uses the new
version (`text_summarizer@0.2`), the old version stays stored, and a behavior-preserving rewrite is
rejected by the gate (`rejected_by_gate`).
