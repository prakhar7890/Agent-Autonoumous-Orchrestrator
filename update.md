

## Current implementation

- Versioned agent manifests use canonical JSON and SHA-256 content hashes.
- Repository lifecycle enforces candidate, approval, stable, deprecated, and rejected states.
- In-memory and Neo4j graph adapters share schema checks, immutable version properties, and
  idempotent natural-identity registration support.
- Capability routing builds explainable, version-pinned plans, inserts safe chainable
  prerequisites, rechecks lifecycle/integrity, and validates plan modality compatibility.
- LangGraph and plain execution paths support approval pauses, bounded retries, token/time/
  step limits, sanitized traces, artifact metadata, and terminal resume-result caching.
- Tool bindings resolve `request.text` and prior-step text before dispatch.
- Runtime output validation checks modality and optional pinned object/array/required-field
  schemas before downstream use.
- PDF embedded-text extraction uses `pypdf`; encrypted, corrupt, scanned, or textless PDFs
  remain explicitly unsupported without OCR.
- Image and audio capabilities provide labeled offline stubs plus opt-in provider paths.
- Feedback proposals store candidate checksums and revalidate integrity before approval.
- FastAPI and the same-origin UI expose registry, planning/execution, traces, artifacts, and
  proposal inspection with sanitized responses and bounded uploads.

## Documentation and handoff

Detailed phase documents remain under `docs/`. The current verification and manager handoff
are in `docs/final-verification.md`, `docs/manager-handoff.md`, and `docs/progress.md`.

## Actual verification in this session

Command:

```powershell
python -m pytest -q -m "not neo4j"
```

Result: **378 passed, 1 skipped, 118 deselected in 40.34s**, with one dependency deprecation
warning. This is offline/memory coverage; Neo4j was not run.

Command:

```powershell
ruff check .
```

Result: **not verified** because Ruff is not installed.

Live Gemini, Groq, image, audio, browser, Neo4j restart, and shared-database checks were not
run in this session. Remaining focused regressions include full terminal persistence checks,
flag-only clarification, complete step retry-policy enforcement, and unavailable live-cost
estimate blocking.

## Release status

Controlled local/offline demonstration only. The project is not production-ready.

## Complete project directory inventory

### Root

- `README.md` — setup, usage, architecture, and limitations.
- `AGENTS.md` — repository safety and process rules.
- `pyproject.toml` — Python package metadata, dependencies, scripts, and pytest settings.
- `package.json`, `package-lock.json` — local JavaScript/browser tooling metadata.
- `update.md` — this consolidated implementation and verification log.
- `final-verification.md` — supplied historical audit input.
- `deploy/optional/docker-compose.yml` — optional deployment experiment; Docker is not required.

### Runtime package: `agent_repo/`

- `manifest.py` — modalities, risk/status enums, pipeline steps, immutable manifests, hashes,
  and optional output-schema checks.
- `contracts.py` — frozen offline domain envelopes for agents, versions, workflows, executions,
  artifacts, feedback, proposals, and approvals.
- `repository.py` — version registry, lifecycle transitions, human approval, dependency and
  integrity enforcement.
- `graph/store.py` — graph schema, in-memory persistence, property immutability, and
  idempotent node registration.
- `graph/neo4j_store.py` — parameterized Neo4j adapter, schema setup, reads/writes, batching,
  and idempotent registration.
- `graph/views.py` — graph inspection and export views.
- `routing.py` — request facts, capability scoring, prerequisite chains, reusable workflows,
  immutable plans, and plan validation.
- `execution/runner.py` — bounded tool/agent execution, bindings, retries, token accounting,
  validation, traces, artifacts, and execution records.
- `execution/engines.py` — plain and LangGraph orchestration, approval pauses, resume checks,
  and terminal-result idempotency.
- `tools.py` — deterministic tools, opt-in Gemini/Groq/Hugging Face integrations, risk policy,
  provider errors, PDF extraction, and modality payloads.
- `llm.py` — mock and LangChain LLM adapters, provider factory, content normalization, and
  untrusted draft generation.
- `feedback.py` — feedback intake, cases, candidate generation, evaluation gates, checksum-bound
  developer approval, rejection, rollback/workflow switching.
- `artifacts.py` — local artifact storage, MIME/size checks, ownership, checksums, and safe paths.
- `config.py` — environment-backed settings, execution limits, provider flags, and Neo4j config.
- `privacy.py` — redaction, secret/PII scrubbing, and user-ID hashing.
- `actors.py` — human/machine actor checks and approval authorization.
- `seeds.py` — bundled seed agents and human-approved seed initialization.
- `cli.py`, `__main__.py` — command-line entry points and offline demo operations.
- `api/` — FastAPI app, transport models, sanitized views/support, and same-origin static UI.

### Tests: `tests/`

The suite covers API behavior, artifacts, CLI, configuration, feedback, graph adapters,
LangGraph/LLM behavior, manifests, privacy, routing, tools, versioning, and offline provider
fakes. Neo4j-marked tests require explicit isolated-database configuration.

### Documentation: `docs/`

Core references include `SPEC.md`, `ARCHITECTURE.md`, `GRAPH_MODEL.md`, `VERSIONING.md`,
`ROUTER_AND_EXECUTION.md`, `EVALUATION.md`, `FEEDBACK_LOOP.md`, `SAFETY_AND_PRIVACY.md`,
`TOOL_INTEGRATIONS.md`, `LLM_PROVIDERS.md`, `UI_API.md`, `LIMITATIONS.md`, `DECISIONS.md`,
`progress.md`, `final-verification.md`, `demo-script.md`, and `manager-handoff.md`. Generated
catalogs, transcripts, routing evaluations, Cypher exports, and mutation records are retained
as project evidence.

### Verification state

Offline memory-oriented verification: **378 passed, 1 skipped, 118 deselected in 40.34s**.
Ruff: **not verified** because it is not installed. Neo4j, live providers, browser tests,
restart persistence, and remaining focused correctness regressions are **not verified**.

