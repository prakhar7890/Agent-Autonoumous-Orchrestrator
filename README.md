# Agent Repository with Autonomous Router (`agent_repo`)

A **versioned, graph-backed repository of multimodal agents** paired with an **autonomous LangGraph router and execution engine** that selects agents from request facts and stored agent metadata, explains its reasoning, executes with explicit breakpoints and scrubbed traces, remembers successful workflows per query type, and improves agents under human control.

---

## Key Capabilities

- **Git-like Multimodal Agent Repository**:
  - Stores agents, immutable SHA-256-hashed `AgentVersion` manifests, tools (with `LOW`, `MEDIUM`, and `HIGH` risk tiers), composite sub-agent delegations, workflows, executions, and lineage in a graph database.
  - Enforces strict version governance (`candidate -> pending_approval -> stable -> deprecated` and `rejected`), append-only history (zero delete operations on `GraphStore`), and transitive tool resolution (`repo.describe(agent)`).
- **Facts-Only Autonomous Router**:
  - Selects the goal agent strictly from stored agent metadata (`purpose`, `usefulness`, `example_queries`) and request facts (attached payload modality + query intent tokens).
  - Never uses hardcoded per-query rules or an intent-to-output-type lookup table.
  - Automatically chains `stable`, `chainable=True` prerequisite agents via BFS when the attached payload modality differs from the goal agent's input modality (e.g., `audio -> audio_transcriber -> text_summarizer`), while blocking any prerequisite that directly or transitively reaches a `HIGH`-risk tool.
- **LangGraph & Plain Execution Engines with Shared Policy**:
  - `PlainEngine` and `LangGraphEngine` share identical node functions (`plan -> approval -> attempt -> learn`, plus `finish_broken` and `finish_declined`) and pure retry/limit/alternative policies.
  - Supports interactive plan approval interrupts (`require_approval=True` via LangGraph `MemorySaver` checkpointer), 8 explicit breakpoints (`no_match`, `missing_input`, `permission_denied`, `max_retries`, `output_invalid`, `max_steps`, `timeout`, `token_budget`), and workflow memory reuse (`QueryType -[:PREFERS]-> Workflow`).
- **Privacy & Governance Guardrails**:
  - Redacts secrets and PII (API keys, bearer tokens, passwords, emails, Luhn-checked payment cards, Aadhaar, PAN, and phone numbers) before storing traces, feedback, or audit logs.
  - Hashes user identifiers with salted HMAC-SHA256 (`UserIdHasher`).

---

## Important Notices & Limitations

1. **Verified Graph Backend — Neo4j AuraDB Free (Cloud) + `InMemoryGraph`**:
   - **Neo4j AuraDB Free** (`neo4j+s://...`) is the verified database backend, alongside the offline JSON-persistable **`InMemoryGraph`**.
   - **AuraDB Free Tier Limits**: AuraDB Free instances support up to **200,000 nodes** and **400,000 relationships**, and **automatically pause after 72 hours of inactivity**. If an instance is paused, `Neo4jGraph` fails early with a clear message instructing you to resume the instance in the [Neo4j Aura Console](https://console.neo4j.io).
   - `deploy/optional/docker-compose.yml` is provided in `deploy/optional/` only as an **unverified optional local alternative**.
2. **Labelled Deterministic Stubs for Multimodal & External Tools**:
   - `text_summarize` and `file_analyze` are real deterministic local implementations.
   - `audio_transcribe`, `image_classify`, `image_generate`, and `email_send` are **deterministic offline stubs** clearly prefixed with `[STUB ...]` in their descriptions and outputs, with a single swap point in [`agent_repo/tools.py`](agent_repo/tools.py) for plugging in real models/APIs.
3. **Human Approver Is a Naming Convention (Not Authentication)**:
   - Promoting an `AgentVersion` to `stable` or issuing a `HIGH`-risk `AuthorityGrant` checks `is_human_name(actor)`, which rejects empty strings and machine identifiers matching `^(llm|system)` (case-insensitively). This is a **naming convention for governance separation, not cryptographic authentication**.
4. **LLM Providers**:
   - Includes a deterministic offline `MockLLM` (explicitly labelled `"MOCK LLM"`) so all tests and offline workflows run with zero API keys, alongside a `LangChainLLM` adapter (`make_llm("gemini:..." | "groq:..." | "openai:...")`) for live providers.

---

## Setup & Installation

### 1. Virtual Environment (Python 3.12)
```powershell
# Using the existing .venv (Python 3.12.15)
.venv\Scripts\python.exe --version

# Install package and dev dependencies in editable mode
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

### 2. Environment Configuration (`.env`)
Copy `.env.example` to `.env` and fill in your values (never commit `.env`):
```ini
# Required for salted HMAC-SHA256 user-id hashing (>= 16 chars)
AGENT_REPO_HASH_SALT=change-me-to-a-random-32-char-secret-salt

# Neo4j AuraDB Free (verified backend)
NEO4J_URI=neo4j+s://<instance-id>.databases.neo4j.io
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=<your-auradb-password>
NEO4J_DATABASE=neo4j

# Optional LLM provider API keys (not needed for MockLLM / offline tests)
GEMINI_API_KEY=
GROQ_API_KEY=
OPENAI_API_KEY=
```

> **Wipe Safety Rule**: `NEO4J_TEST_WIPE` is **never** read from `.env`. It must be set explicitly in your active shell session (`$env:NEO4J_TEST_WIPE="1"`) when you want `pytest` to wipe and run the `neo4j`-parametrised tests against AuraDB. Furthermore, the test wipe prints the target Aura hostname and refuses to run if the database contains node labels not owned by `agent_repo` (unless `$env:NEO4J_TEST_WIPE="force"`).

---

## Running the Test Suite

### 1. Fast Offline Run (`InMemoryGraph` + Unit Tests)
Runs entirely in memory with `MockLLM` in ~2 seconds:
```powershell
.venv\Scripts\python.exe -m pytest -m "not neo4j" -v
```

### 2. Development Smoke Run (`InMemoryGraph` + `neo4j_smoke` on AuraDB)
Runs core graph, versioning, routing, subgraph, and LangGraph smoke tests on both `InMemoryGraph` and live Neo4j AuraDB Free:
```powershell
$env:NEO4J_TEST_WIPE="1"
.venv\Scripts\python.exe -m pytest -m neo4j_smoke -v -s
```

### 3. Full Dual-Backend Suite (`InMemoryGraph` + All `neo4j` Tests on AuraDB)
Runs every behavioural test across both `InMemoryGraph` and live Neo4j AuraDB Free:
```powershell
$env:NEO4J_TEST_WIPE="1"
.venv\Scripts\python.exe -m pytest -v -s
```

---

## Quick Usage Example

```python
from agent_repo.config import Settings
from agent_repo.graph.store import InMemoryGraph
from agent_repo.repository import AgentRepository
from agent_repo.seeds import seed_repository
from agent_repo.tools import builtin_registry
from agent_repo.routing import BagOfWordsEmbedder, Router
from agent_repo.execution.runner import Runner
from agent_repo.execution.engines import LangGraphEngine
from agent_repo.graph.views import execution_subgraph, render_execution_subgraph

# 1. Seed the repository (7 multimodal agents + tools)
settings = Settings.from_env()
store = InMemoryGraph()
tools = builtin_registry()
repo = AgentRepository(store, tools=tools)
seed_repository(repo, author="alice")

# 2. Build the autonomous router, runner, and LangGraph engine
router = Router(repo, embedder=BagOfWordsEmbedder(), config=settings.router)
runner = Runner(repo, tools=tools, router=router, limits=settings.limits)
engine = LangGraphEngine(runner, require_approval=False)

# 3. Execute a query
result = engine.run(query="summarize this text: Sentence one. Sentence two. Sentence three.")
print("Status:", result.status)
print("Output:", result.output)

# 4. Inspect the stored execution subgraph
sub = execution_subgraph(store, result.execution_id)
print(render_execution_subgraph(sub))
```

## Command-Line Interface (Milestone 5)

Install the project editable, then run `agent-repo` (equivalently,
`.venv\Scripts\python.exe -m agent_repo`). The CLI uses `argparse`.
`--graph-file PATH` selects the JSON-persisted in-memory graph; without it, a
configured `NEO4J_URI` in `.env` selects AuraDB. If neither is configured, the
CLI uses an ephemeral in-memory graph. The CLI never accepts or reads
`NEO4J_TEST_WIPE`; that shell-only variable is exclusively for test fixtures.
Credentials are loaded through settings and are not printed by CLI output.

Examples (put global `--graph-file` before or after the command):

```powershell
agent-repo --graph-file .\agent-graph.json agents
agent-repo --graph-file .\agent-graph.json ask "summarize this report: Results improved. Costs fell."
agent-repo --graph-file .\agent-graph.json explain EXECUTION_ID
agent-repo --graph-file .\agent-graph.json versions text_summarizer --lineage --version 0.2
agent-repo --graph-file .\agent-graph.json feedback EXECUTION_ID --user-id alice --rating -1 --comment "The answer missed a key fact."
agent-repo --graph-file .\agent-graph.json case add WORKFLOW_ID --name "omit internal note" --payload "Public summary. Internal note." --must-not-contain "Internal note"
agent-repo --graph-file .\agent-graph.json case run WORKFLOW_ID
agent-repo --graph-file .\agent-graph.json proposals
agent-repo --graph-file .\agent-graph.json proposals generate PROPOSAL_ID
agent-repo --graph-file .\agent-graph.json proposals evaluate PROPOSAL_ID
agent-repo --graph-file .\agent-graph.json approve PROPOSAL_ID --by "A Named Reviewer"
agent-repo --graph-file .\agent-graph.json reject PROPOSAL_ID --by "A Named Reviewer" --reason "Does not meet the requested behavior."
agent-repo --graph-file .\agent-graph.json catalog
agent-repo --graph-file .\agent-graph.json cypher --output .\agent-graph.cypher
agent-repo --graph-file .\agent-graph.json demo
```

`feedback`, `case`, `proposals generate/evaluate`, `approve`, `reject`, and
`demo` require `AGENT_REPO_HASH_SALT` of at least 16 characters in `.env`. A
human approver is a naming convention, not authentication.
The ordered demo uses deterministic offline tools and `MockLLM`; its audio,
image classification, image generation, and email operations remain labelled
stubs, and its missing-input/file breakpoints use actual missing attachments.
It includes a scripted `MOCK LLM: scripted rewrite` that passes the evaluation
gate and a behavior-preserving rewrite that the gate rejects. The demo adds
examples and a candidate workflow to the selected persistent graph, so use a
separate `--graph-file` for a clean demonstration.

---

## Project Documentation

- **[IMPLEMENTATION.md](IMPLEMENTATION.md)** — Full implementation plan, architectural decisions, graph schema, LangGraph state machine, breakpoint rules, evaluation gate specification, and milestone status.
- **[docs/queries.cypher](docs/queries.cypher)** — Named Cypher queries (`agent_lineage`, `agents_using_tool_transitively`, `active_workflow_per_query_type`, `recent_executions_and_outcomes`) verified on Neo4j AuraDB Free.
- **[graphify-out/GRAPH_REPORT.md](graphify-out/GRAPH_REPORT.md)** — Codebase knowledge-graph report (`577 nodes · 1914 edges · 19 communities`).
