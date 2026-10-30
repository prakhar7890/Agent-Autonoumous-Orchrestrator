# Router and Execution

## Routing algorithm (`agent_repo/routing.py`)

Routing is driven only by **facts of the request** plus **stored agent metadata**. There is no
intent-to-output-type table and no `if "pdf" in query` style rule anywhere.

1. **Payload modality from the attachment, not the words.** `infer_payload_modality()` uses, in
   order: an explicit declared modality, the MIME type, the file-extension data table; `text` when
   nothing is attached. The extension/MIME → modality table lives in `routing.py`.
2. **Modality-word rule.** `extract_facts()` drops a modality word (e.g. `image`, `audio`, `file`)
   from the intent tokens **only when the payload is of that same modality**. In "generate an image
   of a report" (text payload) "image" stays, so `image_generator` can match. If the query contained
   *only* a modality word, the full tokens are restored as a fallback.
3. **Score every default stable version.** `score_agent()` computes a similarity against `purpose`,
   `usefulness` and `example_queries` using the pluggable `Embedder` (default `BagOfWordsEmbedder`,
   a deterministic offline matcher). The three sub-scores are blended with configured weights
   (`purpose_weight`, `usefulness_weight`, `examples_weight`); candidates must clear
   `min_score_threshold` **and** have at least one matched term.
4. **Tie-breaks.** Descending score, then preference for an agent whose input modality already equals
   the payload modality, then agent name. Candidates within a small margin of the top score become
   **alternative workflows**.
5. **Prerequisite insertion (BFS).** If the payload modality is not accepted by the goal agent, BFS
   over **`chainable`, `stable`** agents inserts a conversion path (output modality → required input
   modality). A prerequisite that **transitively reaches a HIGH-risk tool** (through sub-agents) is
   excluded. Example: audio payload + summary request → `audio_transcriber` → `text_summarizer`.
6. **Workflow memory.** A composed workflow is stored on its first success as the query type's active
   (`PREFERS`) workflow. On later requests the stored workflow is reused **only when the newly scored
   goal agent equals the stored workflow's goal agent**; overlapping words alone never trigger reuse.
7. **Explain.** Each `PlanStep` records `role`, agent+version, `purpose`, `reason`, `how_it_helps`,
   `matched_terms`, `score`, input→output modalities and reachable tools.

The full text is stored on the `Execution` (`plan_text`, `plan_json`) and the `reason` is stored on
each `INCLUDES` edge, so the explanation is reproducible from the graph
(`agent-repo explain EXECUTION_ID`).

## Execution and shared policy (`agent_repo/execution/runner.py`)

`ExecutionTrace` is an append-only event log; every event field is scrubbed with `scrub_obj` as it is
written. The trace is built from logged events, **never from model-written prose**. Pure policy
functions — `is_hard_stop`, `should_retry_tool`, `check_limits_before_step`,
`check_limits_after_step`, `select_next_alternative`, `validate_step_output` — are shared by
`PlainEngine` and `LangGraphEngine` so both agree on identical scenarios.

Defaults (`ExecutionLimits`, overridable via `AGENT_REPO_*` env vars):

| Limit | Default |
| --- | --- |
| retries per tool call | 3 |
| max steps | 8 |
| timeout | 30.0 s |
| token budget | 20,000 |
| alternative workflows | 1 |

Token use is **not** read from a provider. It is a deterministic offline estimate,
`estimate_tokens()` (`agent_repo/execution/runner.py:163`): `max(10, len(text) // 4 + 12)`, i.e.
roughly **~4 characters per token** plus a small fixed overhead, on the step's text payload. The
token budget is therefore enforced against this estimate (so it is comparable and testable
offline), not against real model usage accounting.

## Breakpoints

| Breakpoint | When it fires | Hard stop? |
| --- | --- | --- |
| `no_match` | No agent cleared the threshold with a matched term. | yes |
| `missing_input` | Required modality absent, wrong modality, or attached file not found. | yes |
| `permission_denied` | HIGH-risk tool called without a valid authority grant. | yes |
| `timeout` | Elapsed time exceeded before/after a step. | yes |
| `token_budget` | Token budget exceeded. | yes |
| `max_retries` | A tool failed after all retries. | no (may try an alternative) |
| `output_invalid` | A step produced empty/wrong-modality output. | no |
| `max_steps` | Step budget exceeded before a step. | no |

Hard stops never retry and never attempt an alternative workflow. When a tool exhausts its retries,
any alternative that would call the **same failing tool** is skipped and the event
`alternative_skipped` is logged (`select_next_alternative`).

## On a final break

The runner records the `Execution` (status `broken`), files an `Escalation`, and returns a message
plus a clarifying question. Additionally:

- If the **goal agent** itself failed, an improvement proposal is opened for its workflow
  (`loop_break:<reason>`).
- If the reason is `no_match`, a **draft** agent definition (`routable: false`, status `draft`) is
  stored as an `AgentRequest`; it can never be routed to.

## Engine parity

- `PlainEngine` runs `node_plan → node_approval → node_attempt → node_learn` directly.
- `LangGraphEngine` runs the same functions as a `StateGraph` with `interrupt_before=["approval"]`
  when `require_approval=True` (see [`ARCHITECTURE.md`](ARCHITECTURE.md)).
- A dedicated test proves both engines produce identical results on the same scenarios because they
  share the policy code.
