# Requirements fulfillment matrix

**Inspection date:** 2026-11-03
**Scope:** Current workspace source and configuration, not old archives. This is a source
inspection baseline for the requested implementation; it is not a test or live-provider
verification report.

**Current disposition:** controlled local prototype. The current cleanup milestone did not run
tests, live providers, or Neo4j. The command/result evidence from the current session's prior
offline and browser runs, plus this cleanup, is recorded in
[`final-verification.md`](./final-verification.md#current-session-evidence-2026-11-03).
Source inspection and historical result entries below are not substitutes for that runtime
evidence.

## Instructions and project inventory

- Read the root [`AGENTS.md`](../AGENTS.md). Its safety/process requirements apply, including:
  no deletion, no `.env` access, no tests unless requested, one milestone at a time, and no
  application/shared Neo4j writes without explicit permission.
- `.github/copilot-instructions.md` is **not present** in the inspected workspace.
- The package is `agent-repo` / import package `agent_repo` (`pyproject.toml`). The declared
  entry points are `agent-repo = agent_repo.cli:main`, `python -m agent_repo`, and the local
  API `python -m agent_repo.api`.
- Project-owned implementation and evidence areas inspected: `agent_repo/` (core, API, graph,
  execution), `tests/`, `playwright/`, `docs/`, root configuration/dependency files, and
  deployment documentation under `deploy/`.
- Generated/dependency/cache/report directories (including `.venv/`, `node_modules/`,
  `build/`, `agent_repo.egg-info/`, pytest/Ruff caches, and browser reports) were not treated
  as application source. `.env` was not read.
- The worktree was already extensively modified before this task. No pre-existing changes
  were reverted. Existing Ollama demos, browser files, and reports are identified below as
  historical evidence; they were not edited in this milestone.

## Requirement status legend

- **Implemented** — supported by current source inspection; runtime/integration verification
  is still called out separately.
- **Partial** — some behavior exists but a stated requirement or a relevant surface is absent
  or inconsistent.
- **Missing** — no implementation was found in the inspected active application paths.
- **Unverified** — the behavior depends on execution, a provider account, browser runtime, or
  Neo4j and was not exercised in this inspection milestone.

## Matrix

| Requirement | Status | Current source/evidence | Required implementation / acceptance evidence |
|---|---|---|---|
| Inspect the current project and preserve unrelated changes | Implemented | Root `AGENTS.md`; `pyproject.toml`; `requirements.txt`; `README.md`; source/test/docs inventory above; dirty worktree preserved | Continue using the current source only. Do not restore deleted archives or unrelated modified files. |
| One Neo4j database, `Project → AgentRepository` and `Project → SessionData`, retaining actual labels and relationships | Implemented in source; Neo4j unverified | `agent_repo/graph/neo4j_store.py` creates `Project`, `AgentRepository`, and `SessionData` using `HAS_AGENT_REPOSITORY`, `HAS_SESSION_DATA`, and `CONTAINS`; `docs/GRAPH_MODEL.md` documents the current schema | Isolated, read-only hierarchy/restart verification. No application database was contacted or changed. |
| Parameterized Cypher, constraints, transactions, idempotency, and no duplicate roots | Partial; Neo4j unverified | `agent_repo/graph/neo4j_store.py` defines constraints and managed reads/writes; root creation uses `MERGE`; durable identities use idempotent methods. Some graph operations and startup adoption need targeted review under safe test conditions. | Offline query-contract tests plus isolated Neo4j tests for concurrent/idempotent root and record creation, project ownership, and restart. Never use database-wide cleanup. |
| All application state in Neo4j; no SQLite or JSON persistence fallback | Implemented in active source; Neo4j unverified | Production CLI `_make_store()` and API startup require Neo4j. `agent_repo/api/support.py::make_store` now delegates to the same Neo4j-only selector and no longer accepts a graph-file path or advertises memory fallback. Offline coverage verifies rejection when Neo4j is absent. Explicit `InMemoryGraph` fixtures remain test-only. | Keep offline refusal coverage. Do not move or replace existing binary artifact storage; verify graph durability only against an isolated Neo4j test target. |
| Preserve existing artifact handling without turning it into a graph fallback | Partial; storage not verified | `agent_repo/artifacts.py` implements `LocalArtifactStore`; graph records retain artifact reference/checksum/owner metadata; `agent_repo/api/app.py` authorizes artifact reads. Binary artifacts are intentionally persisted as files while metadata/history are in Neo4j. | Preserve this existing boundary; test access control, checksum, restart retrieval, and no graph/file fallback for application records. Record the artifact exception explicitly in final architecture docs. |
| Agent definitions/versions/tools/dependencies/workflows/evaluation/governance under AgentRepository | Implemented in source; Neo4j unverified | `agent_repo/graph/neo4j_store.py` scopes the domain labels under `AgentRepository`; `agent_repo/graph/store.py` lists actual labels and relations; `agent_repo/manifest.py`, `repository.py`, `routing.py`, and `feedback.py` implement manifests, versions, workflows, and evaluations | Verify actual relationship membership and project scoping with isolated Neo4j data. Confirm governance records and approval provenance are queryable without changing existing records. |
| Session/request/plan/execution/step/result/feedback/journal/checkpoint state under SessionData | Partial; Neo4j unverified | `agent_repo/graph/neo4j_store.py` classifies session-domain labels. `agent_repo/api/app.py` persists `Session`, `Request`, `Execution`, `ExecutionPlan`, `StepRun`, `ModelInvocation`, `Result`, and artifact references, with links including `HAS_REQUEST`, `HAS_PLAN`, `HAS_EXECUTION`, `HAS_STEP_RUN`, `INVOKED_MODEL`, `PRODUCES`, and `HAS_RESULT`. | Check that every failure/interruption/feedback/checkpoint path is durably attached to the right session and project. Verify restart recovery on an isolated database. |
| Immutable manifests, historical checksums, lineage, and human-only lifecycle transitions | Partial; tests not run | `agent_repo/manifest.py` is frozen/canonicalized; `agent_repo/repository.py` checks hashes and stores lineage/status; `tests/test_manifest_compatibility.py` covers historical canonical encoding; `tests/test_versioning.py` covers lifecycle invariants. Approval metadata is stored on the version/proposal rather than every allowed graph label being an actual standalone approval record. | Run focused checksum/lifecycle tests; add/update tests for exact approval checksum provenance, revoked/incompatible version exclusion, and preserving all historical Ollama records unchanged. |
| Unapproved/revoked/incompatible versions cannot execute; revalidate before dispatch/resume | Implemented in source; tests not run | `agent_repo/repository.py` verifies manifest integrity; `agent_repo/execution/runner.py` rejects non-stable and retired Ollama-pinned versions; `agent_repo/execution/engines.py` revalidates versions after approval/resume. | Focused tests for rejected/deprecated/tampered pins immediately before dispatch and after a paused plan resumes. |
| Documented free-provider policy; account-level eligibility and live billing status | Partial; live/account status unverified | `agent_repo/config.py` and `agent_repo/llm.py` allow only `gemini:gemini-3.8-flash`; `docs/LLM_PROVIDERS.md` records the official-source review and account-level limitations. Groq's current documentation did not establish the needed no-payment/account eligibility, so no Groq fallback is enabled. The application cannot observe the Google account's billing linkage. | Keep the exact allowlist; never infer free billing from `max_cost`. Operator must confirm the project remains on a no-billing Free Tier and meets regional/account eligibility before live use. Add config-rejection tests for unknown/paid models and reconfirm official terms before policy changes. |
| Provider IDs configurable, with optional eligible fallback | Partial | `LLM_PROVIDER` / `LLM_MODEL` exist, but factory/settings enforce a single exact pair. There is intentionally no fallback because no second provider was verified to satisfy the no-payment requirement. | Preserve strict rejection. Only add a separately configured fallback after current official no-payment, capability, privacy, and account eligibility have been verified and tested. |
| Provider timeout, bounded retries, deadline/cancellation, shared rate and concurrency controls | Partial | `agent_repo/llm.py` sets a Gemini request timeout, bounds retry count, rate-limits requests, and uses semaphores. The deadline does not cover the whole operation; cancellation is not exposed. The shared semaphore is keyed by configured concurrency, while `free_provider_slot()` uses the concurrency-1 semaphore, so all configurations do not share the same concurrency gate. | Add a single bounded deadline/cancellation contract and shared provider gate used by central and direct SDK paths. Test lock/rate wait deadlines, cancellation, retry bounds, and no retry amplification. |
| Structured output, normalized provider errors, usage/latency/provider metadata | Partial | `agent_repo/llm.py` requests JSON-schema output and validates it locally; normalizes quota, timeout, and provider errors; captures response tokens/latency. `agent_repo/api/app.py` records `ModelInvocation` events. Missing provider token metadata is estimated, and failed billable calls cannot always report usage. | Test valid/invalid JSON schema, quota exhaustion, timeout, unavailable provider, and metadata on success/failure. Clearly distinguish measured usage from estimates. |
| No active Ollama dispatch; preserve history and reject historical pins | Partial | `agent_repo/llm.py` retires direct Ollama and factory dispatch; `agent_repo/execution/runner.py` returns `provider_retired`; `tests/test_ollama_completion.py` checks dispatch guard. Existing history fixtures/files remain. However, `playwright/ollama.spec.js`, `playwright.ollama.config.js`, and older final-verification reports still describe a live Ollama path. | Keep historical checksums/manifests/approvals/executions unchanged. Clearly mark old browser/report evidence as historical and prevent it from being treated as current setup or default Playwright coverage. |
| New Gemini candidate versions preserve parent and I/O contract; no auto-approval | Offline candidate path covered by prior offline run; persistent Neo4j candidate not created | `register-hosted` creates an unapproved `candidate` pinned to the configured Gemini model. The offline test checks its `SUPERSEDES` edge and that a candidate cannot become the default; the full offline run is linked in the current-session evidence section. No persistent Neo4j candidate was created. | A named human must use the existing approval lifecycle before selecting a candidate as default. Do not promote automatically. |
| Query interpretation, planning, summarization, feedback analysis, and direct SDK paths obey provider policy | Partial | `agent_repo/routing.py` performs deterministic metadata scoring and prerequisite planning; `agent_repo/execution/runner.py` dispatches model-pinned empty-pipeline agents through `make_llm`; `agent_repo/tools.py` has direct Gemini vision dispatch; `agent_repo/feedback.py` calls an injected provider; `agent_repo/cli.py` defaults proposal rewrite to an explicitly labelled mock. Default seeded summarization is a deterministic tool, not hosted inference. | Trace each live call end to end; ensure direct SDK calls share policy/deadlines; ensure mock mode cannot be represented as live output. Verify the default candidate only after human approval. |
| Text-only model cannot masquerade as vision/audio/image generation | Implemented in source; tests not run | `agent_repo/config.py` disables hosted audio transcription and image generation; `agent_repo/tools.py` gates image classification separately and reports other provider paths as unavailable; `tests/test_real_tools.py` contains capability/provider test cases. | Run offline capability rejection tests. Keep audio and image generation unavailable unless a free compatible model is independently verified. |
| Request → pinned plan → pre-dispatch identity → execution/result durability; limits and accurate terminal outcomes | Partial | `agent_repo/api/app.py` creates Request and running Execution before runner dispatch, persists plan/step/model/result events, and records errors/interruption. `agent_repo/execution/runner.py` validates output and enforces step/token/time/tool retry limits. Provider retries and failed-call usage accounting are not fully represented in workflow attempt accounting. | Test persisted identity before dispatch, every terminal outcome, output validation, token/time/step budgets, side-effecting tool uncertainty, and no accidental rerun after crash. |
| Session create/list/switch/reopen, multi-request durability, restart recovery, no stale output | Partial; Neo4j restart unverified | `agent_repo/api/app.py` exposes session routes and request/execution relationships; `agent_repo/api/static/app.js` clears output and guards late responses across session switches; `playwright/ui.spec.js` covers switching/reopening/same-process reload; `playwright/neo4j.spec.js` describes isolated app restart coverage. | Run offline browser suite and, only with verified isolated test Neo4j credentials/data, the dedicated restart browser test. No restart test was executed in this milestone. |
| Authenticated ownership across sessions, requests, executions, artifacts, and project data | Partial; tests not run | Production API middleware derives actors from bearer tokens and hashes identity; session/execution/artifact endpoints check owner/session relationships. `allow_unauthenticated` exists as an explicit app-factory option used in tests. No API cancellation/resume endpoints exist. | Test owner rejection for all resource routes and ensure unauthenticated mode is not reachable through production startup. Add authenticated cancel/resume only if safely supported; preserve current read-only governance API design unless the requirement authorizes controls. |
| Feedback investigation and isolated governed improvement with evaluation/checksum-bound approval | Partial | `agent_repo/feedback.py` stores proposal/evaluation state, generates candidate versions, evaluates cases, and checks candidate checksum on proposal approval; `agent_repo/repository.py` enforces human lifecycle changes. Improvement attribution/taxonomy and a persistent standalone approval-record audit are not complete. | Test outage/tool/prompt/capability/missing-input classification, exact checksum-bound approval, failed evaluation rejection, rollback, and unchanged active versions before approval. |
| UI shows correct provider/version, supports ask/output/errors, sessions, governance actions | Partial | Current UI in `agent_repo/api/static/` has Ask, Agents, Versions, Executions, Proposals, sessions, upload, and failure-clearing behavior. `api/views.py` displays provider/model on agent detail and plan; version list rows do not include provider/model. Approval, cancel, and resume controls are absent. Existing Playwright tests cover several core workflows, stale output, errors, ownership, uploads, and layout. | Extend version rows/detail with exact immutable provider/model metadata; add capability-appropriate authenticated controls only when API lifecycle semantics exist. Expand browser coverage for error/quota, ownership, keyboard/mobile, upload/artifact, and supported controls. |
| Regression, lint/type checks, browser, isolated Neo4j, and free live smoke | Offline regression/lint/browser verified; infrastructure checks unverified | Current milestone: `372 passed, 1 skipped, 120 deselected` from the offline pytest suite; Ruff is clean; all 14 offline Playwright tests pass against `tests/browser_server.py`'s in-memory fixture. Neo4j integration/restart and live inference were not run. No project type checker is configured. | Run Neo4j restart/integration only after confirming the dedicated test target is isolated. Live inference only after confirming account-level no-billing status and region eligibility. These offline results do not prove remaining requirements in this matrix. |
| Current setup/provider/architecture/progress/final-verification documentation | Partial | `README.md`, `docs/ARCHITECTURE.md`, `docs/GRAPH_MODEL.md`, `docs/LLM_PROVIDERS.md`, `docs/UI_API.md`, `docs/progress.md`, and `docs/final-verification.md` exist. `docs/final-verification.md` contains older Ollama and prior test narratives mixed with more recent Gemini/Neo4j claims; historical and current evidence are not consistently separated. | Update docs only after corresponding behavior is implemented and verified. Label historical provider/tests explicitly; report only actual current-session run counts and exact blockers. |

## Official provider sources reviewed

Checked **2026-11-03**. The official references and account-level limitations are recorded in
[`LLM_PROVIDERS.md`](./LLM_PROVIDERS.md). Google lists the currently configured Gemini Flash model
as stable with text output, function calling, and structured outputs, and documents free-tier
input/output quota. Free quota is limited and rate/account/region dependent; Google's unpaid-service
terms allow submitted data to be used to improve services and may involve human review. The
application cannot verify billing linkage or the specific account's quota. No Groq fallback is
selected because the no-payment eligibility requirement was not established.

## Milestone 1 disposition

The initial source-inspection status above remains the requirements backlog; it is not evidence
that every listed requirement is implemented.

## Milestone 2: offline regressions, lint, browser, and cleanup (historical run)

The results in this section record an earlier milestone run and are retained as history; they
are superseded by the later current-session evidence below where the same checks were rerun.

The five failures from the clean baseline were resolved as follows:

| Failing test | Defect or stale expectation | Resolution |
|---|---|---|
| `test_make_store_selects_graph_file` | The API helper called `_make_store(settings, path)` although the active factory accepts one argument; the helper also implied forbidden file/memory persistence fallbacks. | Removed the path argument/fallback and tested that API store selection fails clearly without Neo4j. |
| `test_register_hosted_creates_unapproved_version_with_parent_provenance` | The test expected obsolete `draft` status and a nonexistent `parent_version` field. | Asserted the actual `candidate` status and immutable `SUPERSEDES` relationship; retained the check that a candidate cannot become default. |
| `test_proposals_generate_accepts_llm_option` | The test supplied the removed `--graph-file` CLI argument and no injected graph. | Used the supported in-memory test graph injection while retaining unknown-provider and secret-redaction assertions. |
| `test_ollama_pinned_version_with_tool_pipeline_is_not_dispatched` | `PROVIDER_RETIRED` was returned but lacked a user-facing clarification mapping. | Added a clear retired-provider message and mappings for provider/quota/policy failures. Historical pins remain undispatched. |
| `test_pollinations_flux_bounds_response_size` | The test expected an obsolete live image-network path that the free-only policy rejects. | Replaced the expectation with a denial-before-network assertion. |

Validation from a clean working directory with provider/Neo4j credentials and
`NEO4J_TEST_WIPE` unset:

- Focused regressions: **23 passed, 13 skipped**, 0 failed. Skips were Neo4j-only variants.
- Full offline pytest: **372 passed, 1 skipped, 120 deselected**, 0 failed. The skipped case is
  the Aura CLI smoke test; the 120 Neo4j cases were intentionally excluded.
- Per-file outcomes and backend split are recorded in
  [`final-verification.md`](./final-verification.md): `[memory]` 66 passed/1 skipped,
  backend-independent 306 passed, `[neo4j]` 0 executed/120 deselected (**not verified**).
- Ruff: initial baseline was 121 findings; the first check after the targeted test edits reported
  122 (104 safe autofixes applied, 18 remaining). All remaining findings were reviewed and fixed;
  final `ruff check --no-cache .` reported **All checks passed**. A narrow `BLE001` exception
  remains only at the hosted-provider adapter boundary, where provider SDK exception types are
  normalized; no rules were globally disabled.
- Playwright in that earlier run: **14 passed, 0 failed, 0 skipped**. This was subsequently
  extended to 16 tests; the exact later run is linked below.
- Cleanup actions from that earlier milestone are historical. The current cleanup paths and
  their actual removal evidence are recorded in [`cleanup-log.md`](./cleanup-log.md).
- Live provider inference, Neo4j integration, and application-restart durability remain
  **not verified**. No shared or application Neo4j data was read or written. Passing these
  checks does not close the remaining requirements in the matrix.
Implementation proceeds only after the repository's required milestone handoff.

## Current-session evidence — 2026-11-03 cleanup milestone

This cleanup/documentation milestone ran no tests, lint, provider inference, or Neo4j operation.
It relies only on the following actual results already recorded from this session:

| Check | Command | Result |
|---|---|---|
| Full offline suite | `$env:AGENT_REPO_IMAGE_GENERATOR='stub'; python -m pytest -q -m "not neo4j" -p no:cacheprovider` | **392 passed, 5 skipped, 121 deselected; 0 failed, 0 errors**. Five skips: Aura CLI smoke plus four isolated-Neo4j cases. Python 3.14.7; one Google SDK deprecation warning. See the detailed entry in [`final-verification.md`](./final-verification.md#session-history-and-interrupted-execution-recovery). |
| Ruff | `.venv\Scripts\ruff.exe check .` | **Passed**, Ruff 0.17.3. |
| Deterministic Playwright | `$env:AGENT_REPO_IMAGE_GENERATOR='stub'; $env:AGENT_REPO_IMAGE_CLASSIFIER='stub'; $env:AGENT_REPO_TRANSCRIBER='stub'; Remove-Item Env:\GOOGLE_API_KEY,Env:\GEMINI_API_KEY,Env:\GROQ_API_KEY,Env:\OPENAI_API_KEY -ErrorAction SilentlyContinue; npx --no-install playwright test --config=playwright.config.js --workers=1 --reporter=list` | **16 passed, 0 failed**, 58.2s. The key-removal command only removed those names from the test process; no credential values were read. |
| Cleanup preflight | Exact PowerShell preflight in [`cleanup-log.md`](./cleanup-log.md) checked each named path with `Test-Path`, reparse-point inspection, `git check-ignore -q`, and `git ls-files` | `Pre-delete checks passed for all 10 exact candidates.` |
| Cleanup and absence check | Exact PowerShell `Remove-Item -LiteralPath` loop and `Test-Path` verification are recorded in [`cleanup-log.md`](./cleanup-log.md) | `Removed and verified absent: .pytest_cache, .ruff_cache, agent_repo\__pycache__, agent_repo\api\__pycache__, agent_repo\execution\__pycache__, agent_repo\graph\__pycache__, tests\__pycache__, playwright-report, playwright-report-ollama, test-results`. |

The PowerShell post-removal `git status --short` inspection showed the pre-existing dirty
worktree, including unrelated changes and deletions; none were reverted. A subsequent
`git check-ignore` probe of already-removed directory paths returned nonzero, so no post-removal
ignore result is claimed. Git ignore/tracked/reparse checks did pass in the pre-delete preflight.

### Known gaps

- Neo4j hierarchy/count queries, isolated integration tests, application/Neo4j restart recovery,
  and durable checksum verification: **unverified**; no database was queried or changed.
- Live hosted inference and account-level no-billing/free-tier eligibility: **unverified**; no
  configured eligible key was confirmed and no request was made.
- Persistent hosted-model candidate creation and human approval/default selection: not performed.
- Production approval UI/API, resume behavior, and original `spawn EPERM` cause: **unverified**.
- Passing offline/browser checks do not establish completion of all requirements in this matrix.
