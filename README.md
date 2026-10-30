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

### 1. Install (Python 3.12)

```powershell
python -m venv .venv                                   # create the virtual environment
.venv\Scripts\python.exe --version                     # 3.12.x
.venv\Scripts\python.exe -m pip install -e ".[dev]"    # package + test deps
# Optional live LLM / ASR / vision SDKs (google-genai, groq, langchain-google-genai, langchain-groq, langchain-openai):
# .venv\Scripts\python.exe -m pip install -e ".[providers]"
```

The offline demo (step 3) and offline test suite need **no AuraDB, no LLM keys, and no `providers`
extra — only a non-placeholder `AGENT_REPO_HASH_SALT`** (step 2). `google-genai` and the other
provider packages (`groq`, `langchain-google-genai`, `langchain-groq`, `langchain-openai`) come from
the optional `providers` extra (`pip install -e ".[providers]"`). AuraDB is required only for the
optional Neo4j test tiers.

### 2. Configure `.env`

Copy `.env.example` to `.env` and fill it in. Never commit `.env`.

```ini
# Required for feedback/case/proposal/demo commands (>= 16 chars).
# Generate one:  python -c "import secrets; print(secrets.token_hex(16))"
AGENT_REPO_HASH_SALT=<paste-the-generated-hex-here>

# Neo4j AuraDB Free (the verified graph backend)
NEO4J_URI=neo4j+s://<instance-id>.databases.neo4j.io
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=<your-auradb-password>
NEO4J_DATABASE=neo4j

# Optional LLM provider keys (not needed for MockLLM / offline runs)
# The gemini provider reads GOOGLE_API_KEY (NOT GEMINI_API_KEY).
GOOGLE_API_KEY=
GROQ_API_KEY=
OPENAI_API_KEY=

# Optional real tools (default "stub" keeps everything offline/deterministic).
# AGENT_REPO_TRANSCRIBER: stub | groq   (real ASR via GROQ_API_KEY)
# AGENT_REPO_IMAGE_CLASSIFIER: stub | gemini  (real vision via GOOGLE_API_KEY)
# AGENT_REPO_TRANSCRIBER=stub
# AGENT_REPO_IMAGE_CLASSIFIER=stub
```

If `NEO4J_URI` is set, the graph backend is AuraDB; otherwise the library falls back to the offline
`InMemoryGraph`.

`Settings` **refuses a salt that starts with `change-me`** (the `.env.example` placeholder), so a
copy-paste of the template without editing fails fast instead of hashing with a known value.

### 3. Run the offline demo (no AuraDB, no API keys)

```powershell
.venv\Scripts\python.exe -m agent_repo --graph-file .\agent-graph.json demo
```

`--graph-file` forces the JSON-persisted in-memory graph, so the demo never touches AuraDB. Use a
fresh path for a clean run (the demo writes examples and a candidate workflow into the graph).

### 4. Use the library

```python
from agent_repo.config import Settings
from agent_repo.graph.store import InMemoryGraph
from agent_repo.repository import AgentRepository
from agent_repo.seeds import seed_repository
from agent_repo.tools import builtin_registry
from agent_repo.execution.runner import Runner
from agent_repo.execution.engines import LangGraphEngine

settings = Settings.from_env()
repo = AgentRepository(InMemoryGraph(), tools=builtin_registry())
seed_repository(repo, "alice")
engine = LangGraphEngine(Runner(repo))

result = engine.run(query="summarize this text: Sentence one. Sentence two. Sentence three.")
print(result.status, result.output)
```

---

## Command-line interface

`agent-repo` (equivalently `python -m agent_repo`) uses `argparse`. Put the global `--graph-file`
before or after the subcommand.

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

An optional **local, read-mostly** JSON FastAPI service over the same repository, router and runner
the CLI uses. See [`docs/UI_API.md`](docs/UI_API.md) for the design. It is a thin shell: read-only
except `POST /ask`, which runs a query through the real router/executor. The core package still
imports with none of these dependencies installed; the `ui` extra carries them.

```powershell
pip install -e ".[ui]"
python -m agent_repo.api --graph-file .\agent-graph.json
```

The graph target is chosen at startup by CLI argument only, **never per request**. The default is a
graph file (`.\agent-graph.json`); Aura is used **only** with an explicit `--use-aura` flag (which
requires `NEO4J_URI`), so the API can never write to Aura by accident. `GET /health` reports the
active target (`file <path>`, `Aura`, or `in-memory`) without credentials.

Endpoints: `GET /health`, `GET /agents`, `GET /agents/{name}`, `GET /agents/{name}/versions`,
`GET /executions?limit=`, `GET /executions/{id}` (same content as CLI `explain`), `GET /proposals`
(read-only), and `POST /ask` (multipart `query` + optional `attachment`). There are deliberately
**no** approve/reject/rollback/feedback/delete routes — a test asserts the exact route set so a
write endpoint cannot be added by accident. FastAPI's `/docs` page is available for manual checking.

Security: binds to `127.0.0.1` only (refuses any other host with a clear message), no CORS by
default, every string scrubbed with the CLI's `_safe_text`, sanitized errors (no stack traces),
uploads capped at 1 MB (configurable, 413 over cap) written to generated temp names and deleted
after the run, and `/ask` serialized with a lock.

Plan steps come from the same stored plan the CLI prints, through one shared formatter; a value the
plan did not record is returned as `null` (rendered "not recorded") rather than an invented default
— covered by a reused-workflow test.

Typed output: `/ask` and `/executions/{id}` return `output` as `{kind: "text"|"json"|"image",
text?, mime?, data_base64?}`. An image goal returns `kind="image"` with its mime (the stub SVG is
`image/svg+xml`) and base64 data, capped (default 2 MB; over the cap a text message is returned
instead). Image bytes are **never stored in the graph** — the execution record keeps only the
scrubbed `output_text` — so `/executions/{id}` can only surface `kind="text"`. Untrusted markup
(including an SVG containing `<script>`) is returned as opaque base64 data and never inlined into
any HTML the API serves; the `image_generate` stub XML-escapes the prompt it embeds.

Limitations: local only, no authentication, read-only except `POST /ask`. Verified live this
session on `127.0.0.1` against the default graph file:

```
GET  /health  -> {"status":"ok","target":"file agent-graph.json"}
GET  /agents  -> 7 rows (audio_transcriber, email_sender, file_analyzer, image_classifier,
                         image_generator, meeting_note_taker, text_summarizer)
POST /ask "summarize the key points of this report: Alpha facts. Beta launch."
              -> status=succeeded kind=text goal=text_summarizer@0.2 steps=1 (step score=1.0)
POST /ask "generate an image of a cat"
              -> status=succeeded kind=image mime=image/svg+xml (base64 data, length 244)
```

Binding to `0.0.0.0` is refused with exit code 2.

---
## Tests

Run the fast suite (no Neo4j, ~22 s):

    pytest -q -m "not neo4j"
    # 378 passed, 1 skipped, 118 deselected

Run the full dual-backend suite (requires `NEO4J_TEST_WIPE=1` against a
**throwaway** Neo4j instance, not the shared AuraDB):

    pytest -q
    # 474 passed, 1 skipped  (475 collected)

118 tests are marked `neo4j` and run the same logic against the Neo4j Aura
backend; 357 run against the in-memory backend only.

> **Tiers 2 and 3 DELETE ALL DATA in the configured AuraDB** — the fixture wipes the database before
> each test — and require `NEO4J_TEST_WIPE=1` in the shell. **Never point them at a database you
> care about.** Tier 1 only uses the in-memory backend and deletes nothing.

```powershell
# 1. Offline memory-only tier — no AuraDB, no wipe. ~10 s.
#    475 tests collected · 474 passed · 1 skipped (fast run without Neo4j: 378 passed, 118 deselected)
.venv\Scripts\python.exe -m pytest -m "not neo4j"

# 2. AuraDB smoke tier — DELETES ALL DATA in the configured AuraDB before each test.
#    Collected (--collect-only -q -m neo4j_smoke): 42 smoke tests across both backends.
#    Pass/fail count: not verified in this session (requires a live wipe).
$env:NEO4J_TEST_WIPE="1"; .venv\Scripts\python.exe -m pytest -m neo4j_smoke -v

# 3. Full dual-backend tier — DELETES ALL DATA in the configured AuraDB before each test. ~13 min.
#    Full dual-backend run on 2026-10-20: 355 passed, 1 skipped in 15 min 03 s (356 collected),
#    against Aura.
$env:NEO4J_TEST_WIPE="1"; .venv\Scripts\python.exe -m pytest -q
```

`NEO4J_TEST_WIPE` is read **only from the shell** for a single run — never from `.env` — and the
fixture **prints the target host before wiping it**: `tests/conftest.py:87` (inside
`wipe_neo4j_for_tests`). The single skip is
`tests/test_cli.py::test_cli_agents_command_on_aura_graph[memory]` (an Aura CLI smoke test that is
meaningful only against Neo4j).

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

**Honest limits.** The router is measured at 14/20 and 16/20 on two hand-written paraphrase sets that
are now *development* sets (not unseen benchmarks), and some near-ties route silently to the wrong
agent ([`docs/ROUTING_EVAL.md`](docs/ROUTING_EVAL.md)). `image_generate` and `email_send` are stubs;
only `MockLLM` runs in the tests and demo; `openai` is unverified (no credits); and the optional
`deploy/optional/docker-compose.yml` has never been run. Privacy scrubbing is regex-based (it misses
names/addresses), and "human approver" is a naming convention, not authentication.

---

## Honest status and limitations

1. **Backends.** **Neo4j AuraDB Free is the verified cloud backend** (full dual-backend run on
   **2026-10-20**: **355 passed, 1 skipped in 15 min 03 s** (356 collected), against Aura).
   `InMemoryGraph`
   is the verified offline backend and is JSON-persistable. `deploy/optional/docker-compose.yml` is an
   **optional, unverified** local alternative; it has not been run and must not be described as
   verified.
2. **LLM providers.** The **`MockLLM` remains the only LLM path exercised by this project's tests
   and demo** (deterministic, labelled `MOCK LLM` / `"mock"`). The live `LangChainLLM` path has now
   been verified: **`gemini` (via `gemini-3.5-flash`) and `groq` (via `openai/gpt-oss-20b`) answered a
   real request**, and **`openai` is *not verified*** — the account has no credits (HTTP 429
   `insufficient_quota`). Installing the pinned adapters (`pip install -e ".[providers]"`) is required
   for live calls. Structured content blocks (Gemini 3.x) are normalised to plain text via
   `extract_text()`, re-verified live (see [`docs/LLM_PROVIDERS.md`](docs/LLM_PROVIDERS.md)). Every
   non-listed model/provider is **not verified**.
3. **Tools.** `text_summarize` and `file_analyze` are real deterministic implementations.
   `audio_transcribe` and `image_classify` have **opt-in real implementations** — Groq
   (`whisper-large-v3`) and Gemini vision respectively — selected per tool via
   `AGENT_REPO_TRANSCRIBER=groq` / `AGENT_REPO_IMAGE_CLASSIFIER=gemini`; **both were verified live**
   (see [`docs/TOOL_INTEGRATIONS.md`](docs/TOOL_INTEGRATIONS.md)). They default to **deterministic
   offline STUBs** (outputs prefixed `[STUB …]`, `stub: true`), so the automated tests and demo stay
   offline. `image_generate` and `email_send` are **still stubs only** (no real implementation). The
   swap point is the "TOOL IMPLEMENTATIONS" section of [`agent_repo/tools.py`](agent_repo/tools.py);
   a real tool's output is never labelled `stub`.
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
| §2 `LLMProvider` + offline `MockLLM` | `llm.py` | `test_mock_llm_and_make_llm_factory`, `test_langchain_adapter_and_factory_errors`; live `gemini`/`groq` verified manually in [`docs/LLM_PROVIDERS.md`](docs/LLM_PROVIDERS.md) (`openai` not verified) |
| §2 no secrets in code; `.env` (`.env.example`) | `.env.example`, `config.py` | `test_env_overrides_file`, `test_settings_reject_change_me_placeholder_salt` |
| §2 `InMemoryGraph` (JSON) behind `GraphStore`; guarded dual-backend wipes | `graph/store.py`, `tests/conftest.py` | `test_json_roundtrip`, `test_crud_and_rel_queries`; every `graph`-parametrised test runs on both backends |
| §2 stub tools clearly labelled | `tools.py` | `test_stubs_are_labelled`, `test_stub_outputs_are_deterministic` |
| §2 opt-in real tools (Groq ASR, Gemini vision) | `tools.py`, `config.py` | `test_default_registry_uses_stubs`, `test_registry_selects_real_providers`, `test_unknown_tool_provider_is_rejected`, `test_audio_transcribe_groq_returns_transcript`, `test_image_classify_gemini_parses_and_truncates_labels` plus the rest of `tests/test_real_tools.py`; live run in [`docs/TOOL_INTEGRATIONS.md`](docs/TOOL_INTEGRATIONS.md) |
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

