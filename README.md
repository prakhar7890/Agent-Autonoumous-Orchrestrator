# Agent Repository with Autonomous Router (`agent_repo`)

A **versioned, graph-backed repository of multimodal agents** plus an **autonomous router and
execution engine**. The router selects agents from the *facts of a request* (what was attached and
what the words mean) and each agent's *stored metadata*, explains why, executes with explicit
breakpoints, remembers successful workflows per query type, and improves agents only under human
control.

- **Repository:** agents, immutable `AgentVersion` manifests (SHA-256 content hash), tools with
  risk tiers, composite sub-agents, workflows, executions, feedback and lineage stored in a graph.
- **Router:** goal agent picked only by scoring request terms against stored metadata; no
  intent-to-output table and no hardcoded per-query rules.
- **Execution:** `PlainEngine` and `LangGraphEngine` share the same node functions and retry/limit
  policy; `LangGraphEngine(require_approval=True)` can pause for a human before anything runs.
- **Learning loop:** validated feedback raises a proposal; an untrusted LLM rewrite becomes a
  `candidate`; an evaluation gate decides; a named human approves.

See [`docs/PROJECT_DESCRIPTION.md`](docs/PROJECT_DESCRIPTION.md) for the plain-language overview.

---

## Prototype scope

- **The demo and the seed set ship exactly 7 agents** (`text_summarizer`, `audio_transcriber`,
  `image_classifier`, `image_generator`, `file_analyzer`, `meeting_note_taker`, `email_sender` — see
  [`agent_repo/seeds.py`](agent_repo/seeds.py)).
- **The design allows more agents** without touching routing code: agents are data (a manifest in the
  graph) and new ones are added through the repository API / CLI, not by editing the router.
- **Routing cost grows with the agent count.** For every request the router scores each agent's
  active/default stable version (`Router._candidate_versions()` iterates all agents, then
  `Router.route()` scores every one), so a request is **O(number of agents)**. This is fine at the
  prototype's scale; a production router would need an index/embedding store. The prototype makes
  **no claim that it scales to "any number" of agents** — only that it stays correct at the scale
  tested (a memory-only test routes 3 queries over **200 synthetic agents in ~0.4 s**, budget 5 s;
  **measured on the memory backend only; not measured on AuraDB**).
- **Only the 7 seeded agents are tested** and demonstrated. The 200-agent case is a synthetic
  routing-scale test, not a claim about 200 real agent capabilities.

---

## Quickstart

### 1. Install (Python 3.11+)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
```

`pyproject.toml` is the package metadata and dependency source of truth. `requirements.txt` contains
only the generated, pinned pip install requirements and hashes for the base package plus all
`providers`, `ui`, and `dev` extras; it has no explanatory comments or other setup instructions.
Regenerate it after dependency changes with:

```powershell
uv pip compile pyproject.toml --all-extras --python-version 3.11 --python-platform windows --generate-hashes --no-emit-index-url --no-annotate --no-header --output-file requirements.txt
```

The `--no-deps` editable install adds this project without resolving a second, conflicting set of
dependencies. Offline tests use deterministic, explicitly injected fakes and need no provider key.
Normal CLI/API startup requires Neo4j. `InMemoryGraph` is a unit-test fake, never an application
persistence fallback. The `providers` extra installs only the Gemini adapter and its Google Gen AI
SDK; paid and unverified hosted SDKs are not dependencies.

Non-Python prerequisites: an accessible Neo4j 5 database for normal application startup; Node.js
and npm for Playwright; and the Playwright Chromium binary for browser execution. Install JavaScript
dependencies and the browser separately:

```powershell
npm ci
npx playwright install chromium
```

### 2. Configure `.env`

Copy `.env.example` to `.env` and fill it in. Never commit `.env`.

```ini
# Required for feedback/case/proposal/demo commands (>= 16 chars).
# Generate one:  python -c "import secrets; print(secrets.token_hex(16))"
AGENT_REPO_HASH_SALT=<paste-the-generated-hex-here>

# One configured Neo4j database; use only the intended project/database.
NEO4J_URI=neo4j+s://<instance-id>.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=<configured-privately>
NEO4J_DATABASE=neo4j
NEO4J_PROJECT_ID=agent-repo

# Optional live hosted text inference. Confirm the project's Free Tier and regional eligibility
# in Google AI Studio first; the application cannot verify billing status.
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.8-flash
# Change to "free" only after checking the project in Google AI Studio.
LLM_BILLING_TIER=unverified
LLM_TIMEOUT_SECONDS=30
LLM_MAX_RETRIES=1
LLM_MAX_CONCURRENCY=1
GOOGLE_API_KEY=

# Local API authentication (unique random tokens; never commit them).
AGENT_REPO_API_TOKENS={"alice":"<random-token-at-least-32-characters>"}
AGENT_REPO_HASH_SALT=<private-random-salt-at-least-16-characters>
```

Set `NEO4J_USER` (or its supported legacy alias `NEO4J_USERNAME`), `NEO4J_PASSWORD`,
`NEO4J_DATABASE`, and `NEO4J_URI`. The project creates/uses a single project-scoped hierarchy in
that database. It does not silently select a local file or in-memory persistence backend.

`Settings` **refuses a salt that starts with `change-me`** (the `.env.example` placeholder), so a
copy-paste of the template without editing fails fast instead of hashing with a known value.

### 3. Start the API and UI

```powershell
.\.venv\Scripts\python.exe -m agent_repo.api --port 8000
```

The local service binds only to `127.0.0.1`. It refuses startup if Neo4j or API actor tokens are not
configured. Session, request, plan, execution, result, and artifact-reference history is stored in
Neo4j; there is no JSON-file or SQLite persistence fallback.

### 4. Use the library

```python
# InMemoryGraph is for explicit unit-test fixtures, not normal application startup.
from agent_repo.graph.store import InMemoryGraph
from agent_repo.execution.runner import Runner
from agent_repo.repository import AgentRepository
from agent_repo.seeds import seed_repository
from agent_repo.tools import builtin_registry

repo = AgentRepository(InMemoryGraph(), builtin_registry())
seed_repository(repo, "Test Maintainer")
result = Runner(repo).execute_query("summarize the key points of this report: Alpha. Beta.")
```

---

## Command-line interface

`agent-repo` (equivalently `python -m agent_repo`) uses `argparse` and requires the configured
Neo4j database for application persistence.

| Command | What it does |
| --- | --- |
| `demo` | Ordered Acceptance demonstration (12 phases). |
| `ask QUERY [--attachment PATH]` | Route and execute one query. |
| `explain EXECUTION_ID` | Render the stored execution subgraph. |
| `agents [NAME]` | List agents or describe one. |
| `versions AGENT [--version V] [--lineage]` | List versions / lineage. |
| `feedback EXECUTION_ID --user-id U --rating -1\|1 [--comment C]` | Record feedback. |
| `case add ...` / `case run ...` | Add or run automatic evaluation cases. |
| `proposals [list\|generate\|evaluate] ...` | List or advance a proposal. |
| `approve PROPOSAL_ID --by NAME` / `reject PROPOSAL_ID --by NAME --reason R` | Human gate. |
| `catalog` | Print the generated agent catalog. |
| `cypher [--output PATH]` | Export the whole graph as a Cypher script. |

`feedback`, `case`, `proposals generate/evaluate`, `approve`, `reject` and `demo` require
`AGENT_REPO_HASH_SALT` (>= 16 chars). The CLI never accepts or reads `NEO4J_TEST_WIPE`.

---

## Run the API

The local FastAPI service uses the same Neo4j-backed repository, router, and runner as the CLI.
The root `requirements.txt` includes the UI dependencies; after installing it and the project
editable, configure Neo4j and actor-specific API tokens in `.env`, then run:

```powershell
.\.venv\Scripts\python.exe -m agent_repo.api --port 8000
```

The server binds to `127.0.0.1`, requires `AGENT_REPO_API_TOKENS` and
`AGENT_REPO_HASH_SALT`, and refuses to start without `NEO4J_URI`, `NEO4J_USER`,
`NEO4J_PASSWORD`, and `NEO4J_DATABASE`. `InMemoryGraph` is used only by explicit tests. No graph
file, SQLite, or memory fallback is available in normal application startup.

The browser uses bearer-token auth and keeps the token only in memory. Session ownership derives
from the configured actor name and stable salt. Session switching, saved execution recovery,
idempotency, uploads, owner-scoped artifact reads, and sanitized failures are covered in
[`docs/UI_API.md`](docs/UI_API.md). Live model use is governed by the free-only Gemini policy in
[`docs/LLM_PROVIDERS.md`](docs/LLM_PROVIDERS.md).

---
## Tests

Run the offline suite and lint (Neo4j-marked tests and live provider calls are excluded):

```powershell
.\.venv\Scripts\python.exe -m pytest -q -m "not neo4j"
.\.venv\Scripts\python.exe -m ruff check .

npx --no-install playwright test --config=playwright.config.js --workers=1 --reporter=list
```

There is no configured Python static type checker in this project. The Playwright suite uses
`package.json` and `package-lock.json`; it is not installed through `requirements.txt`.
The deterministic Playwright fixture injects `InMemoryGraph` and proves UI/session behavior only;
its refresh checks do not prove application-process or Neo4j restart durability. Real Neo4j
integration tests are opt-in and must only be run with a verified isolated test database. See
[`docs/final-verification.md`](docs/final-verification.md) for current evidence and blockers.

Legacy dual-backend pytest markers may write to or wipe the configured Neo4j database. Do not run
those tests against a shared or application database. The isolated session-history verification
is a separate opt-in browser suite and requires a fresh, explicitly configured test database;
see [`docs/final-verification.md`](docs/final-verification.md). Neo4j verification was not run in
the latest offline test session.

---

## Layout

```
agent_repo/
  manifest.py          # frozen AgentManifest, Modality/RiskTier/Status, content hash
  repository.py        # versioning + governance rules, describe(), catalog_markdown()
  routing.py           # fact extraction, scoring, BFS chain insertion, workflow memory
  tools.py             # ToolRegistry (risk + grants + audit), real tools, opt-in real ASR/vision, stubs
  privacy.py           # scrub()/scrub_obj() regex rules, salted-HMAC UserIdHasher
  actors.py            # human-name convention (NOT authentication)
  feedback.py          # feedback validation, cases, eval gate, proposal lifecycle
  llm.py               # LLMProvider, MockLLM, LangChainLLM, make_llm()
  seeds.py             # the 7 seed agents
  config.py            # Settings, RouterConfig, ExecutionLimits (from env/.env)
  cli.py               # argparse CLI
  graph/store.py       # GraphStore protocol + shared schema/immutability + InMemoryGraph
  graph/neo4j_store.py # Neo4j 5 / AuraDB backend
  graph/views.py       # execution_subgraph(), text renderer, export_cypher()
  execution/runner.py  # shared policy, event-sourced trace, breakpoints, Runner
  execution/engines.py # PlainEngine + LangGraphEngine (shared node functions)
  api/                 # local FastAPI service (optional `ui` extra): app factory, models, views, support, __main__
docs/                  # M6 documentation (below)
tests/                 # full test suite (see Tests)
deploy/optional/       # unverified optional docker-compose (see limitations)
```

---

## How to explain this project (plain language)

**The architecture in five sentences.** Agents are stored as data — frozen, versioned manifests (an
ID, input/output modalities, a risk tier, a pipeline of tool/sub-agent steps, example queries) held in
a graph — and nothing about an agent lives in code. A router reads a request's *facts* (what was
attached, what the words mean) and scores them against every agent's *stored metadata*, then returns a
`Plan` with an explanation and explicit breakpoints. An execution engine (`PlainEngine` or
`LangGraphEngine`, sharing the same node functions) runs the plan step by step through a tool registry
that enforces risk tiers and authority grants in code and records an event-sourced trace. A feedback
loop turns validated user ratings and failing automatic cases into a `Proposal`, has an untrusted LLM
draft a `candidate` rewrite of only the `system_prompt` and `pipeline`, and lets an evaluation gate
decide whether it is worth a human's attention. Only a named human can ever promote a version to
`stable` or re-point a query type's workflow; machine authors (`llm*`, `system*`) are refused.

**How the router scores.** For each agent's active stable version it extracts the request's intent
words and the agent's metadata words (normalising modality synonyms such as `image`/`picture`/`photo`
to one token), counts weighted term overlaps (`purpose`, `usefulness`/example-query overlap), and keeps
candidates above a configured `min_score_threshold`. If several payload-compatible candidates sit
within a configured `tie_break_margin` of the top score, it prefers the one whose input modality
matches the attachment, inserting a chainable prerequisite via breadth-first search when needed — all
weights and thresholds are configuration (`RouterConfig`), never hardcoded rules or an
intent-to-output lookup table.

**What the LLM may and may not change.** An LLM may propose a rewrite of *only* `system_prompt` and
`pipeline`, and only on an agent that already has a first human-authored version. It may **not** create
an agent's first version, change I/O modalities, add an unknown or HIGH-risk tool (directly or through
a sub-agent), create a `stable` version, or reach a HIGH-risk tool without an explicit authority
grant — every one of these is enforced in code, not just in the prompt.

**Honest limits.** The router is measured at 14/20 and 16/20 on two hand-written paraphrase sets
that are development sets, not unseen benchmarks; near-ties can route incorrectly
([`docs/ROUTING_EVAL.md`](docs/ROUTING_EVAL.md)). Hosted inference is allowlisted to
`gemini:gemini-3.8-flash` only; no live request has been made for this migration. The unpaid Gemini
terms allow human review and product-improvement use of submitted content, so do not submit
sensitive data. The UI warns that uploaded files, including PDFs or their extracted text, may be
sent to Gemini. `LLM_BILLING_TIER=free` is a required operator assertion for dispatch, not an
automated account-billing check; known paid-tier environment indicators block dispatch. Audio
transcription and image generation are unavailable; email sending remains a stub. Privacy scrubbing
is regex-based and does not catch all personal data. A human approver name is a governance
convention, not authentication.

---

## Honest status and limitations

1. **Persistence.** Neo4j is the only application datastore. `InMemoryGraph` is an explicit test
   fake and is not selected by normal CLI/API startup. Prior AuraDB and file-backed verification
   counts in this document's linked evidence are historical; they do not prove this migration's
   Neo4j hierarchy or restart behavior. Current Neo4j tests were not run unless listed in
   [`docs/final-verification.md`](docs/final-verification.md).
2. **LLM providers.** `gemini:gemini-3.8-flash` is the sole allowlisted hosted pair; the provider
   factory rejects paid, unknown, and retired providers. `MockLLM` is explicitly injected only for
   offline tests. Live Gemini remains **not verified** until an operator verifies that the Google
   project is on the no-billing Free Tier and eligible in the applicable region. Free-tier terms,
   rate limits, and sources are in [`docs/LLM_PROVIDERS.md`](docs/LLM_PROVIDERS.md).
3. **Tools.** `text_summarize` and `file_analyze` are deterministic. `image_classify` can be
   explicitly enabled with Gemini vision; audio transcription and image generation are unsupported
   stubs, and `email_send` does not send. Earlier Groq and Gemini 3.5 tool calls are historical, not
   active integrations; see [`docs/TOOL_INTEGRATIONS.md`](docs/TOOL_INTEGRATIONS.md).
4. **Human approver = naming convention, not authentication.** `is_human_name()` rejects empty
   names and names starting with `llm`/`system` (case-insensitive). It does not authenticate anyone.
5. **Privacy scrubbing is regex-based.** It catches well-formed secrets/cards/emails/phones/IDs, but
   **not personal names, addresses, or free-form personal data**. Because phone rules are
   digit-pattern based, **some phone-like numbers in free text may be redacted**. Free text is
   scrubbed **at write time**; exports (`cypher`) preserve stored ids and content hashes (a
   hash is not a secret). See [`docs/SAFETY_AND_PRIVACY.md`](docs/SAFETY_AND_PRIVACY.md).
6. **LangGraph checkpointer.** With the default `MemorySaver`, resuming a paused plan emits
   `Deserializing unregistered type …` warnings on stderr (a future LangGraph change may require an
   allow-list). Execution still succeeds; the warnings are cosmetic for this prototype. This
   behaviour is version-specific, so `langgraph` and `langgraph-checkpoint` are **pinned**
   (`langgraph==1.2.15`, `langgraph-checkpoint==4.3.0`) in [`pyproject.toml`](pyproject.toml) — see
   [`docs/DECISIONS.md`](docs/DECISIONS.md).

---

## Requirements covered

Where each requirement in [`docs/SPEC.md`](docs/SPEC.md) and each binding decision in
[`docs/DECISIONS.md`](docs/DECISIONS.md) (the manager's answers) is implemented and tested. Test
names are the base names; graph-parametrised tests also run as `[memory]` and `[neo4j]`.

| Requirement (SPEC / decision) | Module | Tests |
| --- | --- | --- |
| §2 Python 3.11+/pydantic v2/pytest | `pyproject.toml` | whole suite |
| §2 LangGraph control flow + LangChain model access | `execution/engines.py`, `llm.py` | `test_plain_engine_and_langgraph_engine_give_identical_results`, `test_mock_llm_and_make_llm_factory`, `test_langchain_adapter_and_factory_errors`, `test_langgraph_mermaid_diagram_has_all_nodes` |
| §2 Neo4j 5 driver; optional docker-compose | `graph/neo4j_store.py`, `deploy/optional/docker-compose.yml` | `test_cli_agents_command_on_aura_graph` (`[neo4j]`); **docker-compose not verified** |
| §2 free-only `LLMProvider` + offline `MockLLM` | `llm.py`, `config.py` | `test_mock_llm_and_make_llm_factory`, `test_free_model_configuration_and_exact_factory_dispatch`, `test_provider_failures_are_normalized`; live Gemini not verified |
| §2 no secrets in code; `.env` (`.env.example`) | `.env.example`, `config.py` | `test_env_overrides_file`, `test_settings_reject_change_me_placeholder_salt` |
| §2 Neo4j application persistence; explicit in-memory test fake | `graph/neo4j_store.py`, `graph/store.py` | Neo4j-specific checks require verified isolated DB configuration; do not infer durability from in-memory tests |
| §2 stub tools clearly labelled | `tools.py` | `test_stubs_are_labelled`, `test_stub_outputs_are_deterministic` |
| §2 tool capability policy | `tools.py`, `config.py` | `test_audio_and_image_generation_are_explicitly_non_live_stubs`, `test_image_classifier_uses_the_allowlisted_free_gemini_vision_model`; fake responses only |
| §3 manifest fields + validation | `manifest.py` | `test_valid_manifest`, `test_required_fields`, `test_needs_pipeline_or_prompt`, `test_sub_agent_must_pin_version`, `test_unknown_fields_rejected`, `test_manifest_is_frozen` |
| §3 `describe()` incl. tools via sub-agents + catalog | `repository.py`, `seeds.py` | `test_describe_has_every_manifest_field`, `test_composite_reports_tools_via_sub_agents`, `test_transitive_high_risk_detection`, `test_catalog_lists_all_agents_and_stubs` |
| §4 graph nodes/rels, JSON payloads, `execution_subgraph` + renderer | `graph/store.py`, `graph/views.py` | `test_schema_is_enforced`, `test_graph_edges_for_tools_and_sub_agents`, `test_workflow_memory_reuse_and_execution_subgraph` |
| §4 Cypher export + `docs/queries.cypher` | `graph/views.py` | `test_cypher_export_of_demo_graph_is_verbatim` |
| §5 immutable version + content hash + integrity check | `manifest.py`, `repository.py` | `test_hash_is_canonical_and_content_sensitive`, `test_manifest_cannot_be_updated_in_place`, `test_integrity_check_detects_tampering`, `test_change_creates_new_version_linked_by_supersedes` |
| §5 nothing hard-deleted; "delete" = deprecated | `repository.py`, `graph/store.py` | `test_store_has_no_delete_operation`, `test_deprecate_keeps_version_stored`, `test_old_versions_remain_after_new_ones` |
| §5 status lifecycle + illegal transitions refused | `repository.py` | `test_transition_table`, `test_new_versions_always_start_as_candidate` |
| §5 only a named human promotes to `stable`; `llm*`/`system*` refused | `actors.py`, `repository.py` | `test_only_named_humans_promote_to_stable`, `test_non_humans_cannot_deprecate_or_set_default` |
| §5 improvement applies to the query type, not the default version | `feedback.py` | `test_human_approval_repoints_query_workflow_without_changing_agent_default`, `test_approving_later_version_keeps_default` |
| §5 version prompts/model/tools/sub-agents | `repository.py` | `test_everything_is_versioned`, `test_sub_agent_change_is_versioned` |
| §6 state graph `plan→approval→attempt→learn`, interrupt, Mermaid | `execution/engines.py` | `test_langgraph_mermaid_diagram_has_all_nodes`, `test_langgraph_approval_interrupt_pause_and_resume`, `test_plain_engine_and_langgraph_engine_give_identical_results` |
| §6 routing from facts (payload + intent scoring + matched terms) | `routing.py` | `test_routing_modalities_and_auto_chain_insertion`, `test_symmetric_modality_word_normalisation_and_payload_compatible_margin` |
| §6 BFS prerequisite insertion, `chainable`, transitive HIGH-risk block | `routing.py` | `test_bfs_never_inserts_chainable_prerequisite_reaching_high_risk`, `test_transitive_high_risk_detection` |
| §6 modality-word drop rule | `routing.py` | `test_routing_modalities_and_auto_chain_insertion` |
| §6 no hardcoded rules; configurable weights; pluggable embedder | `routing.py`, `config.py` | `test_routing_modalities_and_auto_chain_insertion`, `test_env_overrides_file` |
| §6 explainability stored on `Execution`/`INCLUDES`; scrubbed trace | `routing.py`, `execution/runner.py`, `graph/views.py` | `test_workflow_memory_reuse_and_execution_subgraph`, `test_stored_trace_tokens_used_is_int_matching_runner_count` |
| §6 eight breakpoints, hard stops, `alternative_skipped`, limits | `execution/runner.py` | `test_real_breakpoints_no_match_missing_input_and_permission_denied`, `test_runtime_breakpoints_retries_alternative_skipped_and_limits` |
| §6 escalation + draft agent request; workflow-memory reuse | `execution/runner.py`, `routing.py`, `feedback.py` | `test_workflow_memory_reuse_and_execution_subgraph`, `test_goal_agent_loop_break_opens_proposal_and_deduplicates` |
| §7.1 feedback validation, hashed ids, scrub, hourly rate limit | `feedback.py`, `privacy.py` | `test_feedback_requires_identified_user_valid_rating_and_negative_comment`, `test_feedback_is_hashed_scrubbed_and_one_per_user_per_execution`, `test_hourly_feedback_limit_is_enforced_per_hashed_user` |
| §7.2 triggers (N users / case fail / loop break); one open proposal | `feedback.py` | `test_negative_feedback_from_distinct_users_opens_one_proposal`, `test_failing_automatic_case_triggers_one_proposal`, `test_goal_agent_loop_break_opens_proposal_and_deduplicates` |
| §7.3 LLM rewrite limited to `system_prompt`+`pipeline`, guardrails | `feedback.py`, `repository.py` | `test_candidate_rewrite_is_labelled_mock_and_changes_only_allowed_fields`, `test_fenced_json_rewrite_is_accepted_and_prompt_includes_context`, `test_invalid_rewrite_shapes_fenced_garbage_extra_keys_and_modality_change_fail_safely`, `test_rewrite_cannot_add_unknown_or_high_risk_tool_directly_or_transitively` |
| §7.4 eval gate (strict accuracy, no regression, latency/token, `needs_cases`) | `feedback.py` | `test_eval_gate_requires_strict_accuracy_improvement_without_regression`, `test_eval_gate_rejects_useless_mock_rewrite`, `test_eval_gate_rejects_regression_even_when_accuracy_improves`, `test_eval_gate_enforces_latency_and_token_budgets`, `test_no_cases_means_needs_cases_and_never_approves` |
| §7.5 human approval, rollback, old version kept | `feedback.py` | `test_human_approval_repoints_query_workflow_without_changing_agent_default`, `test_rollback_repoints_to_prior_workflow_and_requires_human`, `test_human_rejection_rejects_candidate_and_keeps_old_workflow` |
| §8 tool risk tiers, code-enforced grants, audit log | `tools.py` | `test_high_risk_denied_without_grant_and_audited`, `test_high_risk_allowed_with_human_grant`, `test_invalid_grants_are_refused`, `test_low_risk_needs_no_grant`, `test_audit_is_scrubbed` |
| §8 scrub secrets/PII before storing; hashed user ids | `privacy.py` | `test_scrub_patterns`, `test_scrub_obj_nested_and_sensitive_keys`, `test_hashing_is_salted_and_deterministic`, `test_hasher_refuses_weak_salt`, `test_digit_rules_do_not_match_inside_alphanumeric_tokens` |
| §8 the 7 seed agents (chainable / composite / HIGH-risk) | `seeds.py` | `test_seven_seeds_all_stable`, `test_only_transcriber_is_chainable`, `test_composite_reports_tools_via_sub_agents` |
| §9 each milestone's invariants tested | `tests/` | whole suite |
| §10 documentation set | `docs/` | (documents, not tests) |
| §11 ordered demo (12 phases) | `cli.py` | `test_demo_runs_ordered_workflow_and_both_rewrite_gate_outcomes`, `test_demo_end_to_end_shows_stored_label_gate_rejection_and_real_breakpoint`, `test_demo_redacts_a_secret_looking_string_from_demo_content` |
| §12 a rule without a test does not count; honesty in docs | `tests/`, `README.md`, `docs/` | whole suite; see "Honest status and limitations" |
| Decision: `argparse` only; no credential/wipe options | `cli.py` | `test_argparse_cli_persists_graph_and_supports_read_commands`, `test_argparse_errors_do_not_echo_credentials` |
| Decision: cases support exact-field and must-not-contain | `feedback.py` | `test_cases_support_exact_fields_and_must_not_contain` |
| Decision: feedback limit 10/h; negative comment ≥ 8 chars | `feedback.py` | `test_hourly_feedback_limit_is_enforced_per_hashed_user`, `test_feedback_requires_identified_user_valid_rating_and_negative_comment` |
| Decision: demo breakpoints must be real, not fabricated | `cli.py` | `test_demo_end_to_end_shows_stored_label_gate_rejection_and_real_breakpoint` |
| Decision: demo has a `MOCK LLM` rewrite and one gate-rejected rewrite | `cli.py`, `feedback.py` | `test_demo_runs_ordered_workflow_and_both_rewrite_gate_outcomes`, `test_eval_gate_rejects_useless_mock_rewrite` |
| Decision: AuraDB verified, docker-compose optional/unverified | `graph/neo4j_store.py` | see limitation 1 (docker-compose **not verified**) |
| Decision: placeholder salt rejected | `config.py` | `test_settings_reject_change_me_placeholder_salt` |
| Decision: `langgraph`/`langgraph-checkpoint` pinned | `pyproject.toml` | whole engine suite |

---

## Documentation

- [docs/PROJECT_DESCRIPTION.md](docs/PROJECT_DESCRIPTION.md) — what it does, inputs, workflow, outputs, next.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, data flow, sequence + LangGraph diagrams.
- [docs/GRAPH_MODEL.md](docs/GRAPH_MODEL.md) — nodes, relationships, properties, views, export.
- [docs/VERSIONING.md](docs/VERSIONING.md) — status state machine and governance rules.
- [docs/ROUTER_AND_EXECUTION.md](docs/ROUTER_AND_EXECUTION.md) — routing algorithm, breakpoints, trace.
- [docs/FEEDBACK_LOOP.md](docs/FEEDBACK_LOOP.md) — feedback, proposals, rewrite guardrails.
- [docs/SAFETY_AND_PRIVACY.md](docs/SAFETY_AND_PRIVACY.md) — risk tiers, grants, scrubbing, hashing.
- [docs/EVALUATION.md](docs/EVALUATION.md) — cases, eval gate, goals and constraints.
- [docs/ROADMAP_AND_OPEN_QUESTIONS.md](docs/ROADMAP_AND_OPEN_QUESTIONS.md) — next steps and open questions.
- [docs/AGENT_CATALOG.md](docs/AGENT_CATALOG.md) — catalog generated by `agent-repo catalog`.
- [docs/queries.cypher](docs/queries.cypher) — named Cypher queries (lineage, tools per agent, active workflow).
- [docs/graph_export.cypher](docs/graph_export.cypher) — full Cypher export produced by `agent-repo cypher`.
- [docs/DECISIONS.md](docs/DECISIONS.md) — binding decisions where the spec was ambiguous.
- [docs/MUTATION_CHECKS.md](docs/MUTATION_CHECKS.md) — mutation-style evidence for the safety rules.
- [docs/UI_API.md](docs/UI_API.md) — local read-mostly FastAPI service: endpoints, security rules, typed output.
- [docs/SPEC.md](docs/SPEC.md) — original requirements (reference only).

## Durable sessions and browser verification

Sessions, requests, plans, executions, step runs, results, model metadata, and artifact references
are persisted to Neo4j; large artifact bytes stay outside the graph. The browser keeps the bearer
token in memory and does not use browser storage for history or request content. The deterministic
browser fixture uses an in-memory graph and is not restart-durability evidence. Real Neo4j
verification must use a dedicated, verified isolated database named
`agent_repo_test_<unique suffix>`.

On startup, prior `pending`/`running` executions are marked `interrupted` without being
redispatched. Their session history remains inspectable; the authenticated owner can cancel an
interrupted execution. Session restoration returns saved requests, plans/version pins, executions,
results, and feedback. The API/browser refresh tests use a test graph fake and do not prove a
Neo4j/container restart; that remains unverified until the isolated test target is available.

```powershell
# Start the API/UI using the configured Neo4j database.
.\.venv\Scripts\python.exe -m agent_repo.api --port 8765

# Full offline Python regression suite (no Neo4j writes or live providers).
.\.venv\Scripts\python.exe -m pytest -q -m "not neo4j"

# Deterministic Playwright suite; starts its in-memory test fixture.
npx --no-install playwright test --config=playwright.config.js --workers=1
```

The HTML report is `playwright-report/index.html`; screenshots and traces are under
`test-results/`. Real Neo4j and provider verification prerequisites, coverage limits, and current
evidence are documented in [`docs/UI_API.md`](docs/UI_API.md) and
[`docs/final-verification.md`](docs/final-verification.md). In-memory browser refresh tests do not
count as proof of Neo4j durability.
#   A g e n t - A u t o n o m o u s - O r c h e s t r a t o r  
 