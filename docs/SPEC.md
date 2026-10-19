# PROMPT FOR ANTIGRAVITY: Agent Repository with Autonomous Router

You are a senior Python engineer. Build a working prototype of a **versioned, graph-backed repository of multimodal agents** plus an **autonomous agent** that picks agents from that repository for a human query, shows its reasoning, executes, and improves agents over time under human control.

**Before writing any code:** produce an implementation plan and task list, and wait for my approval. After approval, work milestone by milestone (section 9). At the end of each milestone: run the tests, show me the results, and stop for my go-ahead.

---

## 1. Goal in plain words

1. A "git-like" repository of agents. Each agent does ONE task type (image classification, image generation, audio transcription, file analysis, text summarization, and so on). "Multimodal" means the repository holds many such agents, each with its own input and output type.
2. Agents, their versions, tools, sub-agents, workflows and relations are stored in a **Neo4j graph database**. Every agent carries metadata: what it does, why it is useful, what input it takes, how it processes a query, which tools it uses, and what output type it produces.
3. An **autonomous agent** reads the human query, decides which repository agents to use, **explains why** (what each agent does and how it helps), stores that plan as a **subgraph**, shows it to the user, then executes it.
4. The plan comes from **the facts of the request** (what was attached, what the words of the request mean, what each agent's stored metadata says). It must never be driven by hardcoded per-query rules.
5. When agents cannot produce the expected result, the loop has an explicit **breakpoint**.
6. When users are not satisfied, the system proposes a **new version** of the agent for that query type. After checks and **human approval**, that version is stored, and the next similar query retrieves that workflow.

## 2. Stack and constraints

- Python 3.11+, `pydantic` v2, `pytest`.
- **LangGraph** for the autonomous agent's control flow; **LangChain** for model access.
- **Neo4j 5** (Community) via `docker-compose.yml`, official `neo4j` Python driver.
- LLM providers: Gemini, Groq, OpenAI behind one `LLMProvider` interface (LangChain chat-model adapter). Include a deterministic **`MockLLM`** so everything runs and tests pass offline with no API keys.
- Runs locally first. No secrets in code. Keys and the hashing salt come from `.env` (provide `.env.example`; `.env` is git-ignored).
- Provide a second graph backend, **`InMemoryGraph`** (JSON-persistable), behind the same `GraphStore` interface, so tests run without Docker. Every behavioural test must also run against Neo4j when `NEO4J_URI` is set (guard destructive wipes behind `NEO4J_TEST_WIPE=1`).
- Image, audio and image-generation tools are **clearly labelled stubs** (deterministic, offline) with one obvious place to swap in a real model. Never present a stub as real capability.

## 3. Agent manifest (every agent version must have these)

`purpose` (what it does), `usefulness` (why it is useful), `input_modalities` + `input_description`, `processing_description` + `pipeline` (ordered steps; each step is a tool call or a sub-agent call, with params), `system_prompt`, `output_type` + `output_description`, `example_queries`, `chainable` (may the router insert it automatically as a prerequisite, e.g. transcription before summarization), `provider`, `model`.
Validation: purpose and usefulness not empty, at least one input modality, at least a pipeline or a system prompt.
Provide `describe(agent)` returning all of the above, including the **tools reachable through sub-agents** (a composite agent has none of its own; do not report an empty tools list for it), and a generated markdown catalog.

## 4. Graph model

Nodes: `Agent`, `AgentVersion`, `Tool` (with risk tier), `QueryType`, `Workflow`, `Execution`, `Feedback`, `Case` (automatic-test case), `Proposal`, `Escalation`, `AgentRequest`.
Relationships: `HAS_VERSION`, `SUPERSEDES`, `USES_TOOL`, `DELEGATES_TO`, `PREFERS` (query type to its active workflow), `INCLUDES` (workflow to agent version, with `order`, `role` and `reason` stored **on the edge**), `RAN`, `OF_TYPE`, `GOT`, `HAS_CASE`, `HAS_PROPOSAL`, `PROPOSES`, `ESCALATED`, `REQUESTED`.
Store large payloads (manifest JSON, trace JSON, the plan as shown to the user) as JSON string properties; keep graph structure for lineage and relationships.
Provide `execution_subgraph(execution_id)` and a text renderer: execution, query type, workflow, each agent version with its stored reason, and the tools behind it (including tools of sub-agents).
Provide a Cypher export so the graph can be loaded into Neo4j Browser, and a `docs/queries.cypher` of useful queries (lineage, which agents use a tool, active workflow per query type).

## 5. Versioning rules (enforce in code, not by convention)

- An `AgentVersion` manifest is **immutable**. Any change creates a new version linked by `SUPERSEDES`. Store a content hash and provide an integrity check that detects tampering.
- **Nothing is hard-deleted.** "Delete" means `deprecated`. Old versions stay, because version 0.1 may handle queries that 0.2 does not.
- Status lifecycle: `candidate -> pending_approval -> stable -> deprecated`, plus terminal `rejected`. Reject illegal transitions.
- **Only a named human can promote a version to `stable`.** Authors or reviewers named `llm*` or `system*` must be refused.
- A learned improvement applies to a **query type**, not globally: approving 0.2 must not change the agent's default version.
- Version everything the manager listed: prompts, model choice, tools, sub-agents.

## 6. The autonomous agent (LangGraph state graph)

Flow: `plan -> approval -> attempt -> learn`, with `attempt` looping back to itself for the next alternative workflow, and exits to `finish_broken` and `finish_declined`. Support an optional `require_approval` mode using a LangGraph interrupt, so the plan can be shown and approved before anything runs. Show the Mermaid diagram in the docs.

**Routing from facts, not hardcoded rules:**
- The payload type comes from what the user actually attached (audio, image, file) or plain text.
- Match the intent words of the query against each agent's stored `purpose`, `usefulness` and `example_queries` (a relevance score, plus the matched terms for the explanation).
- Insert prerequisite agents by input/output modality compatibility (BFS), only from agents marked `chainable`, and never one that can reach a HIGH-risk tool (check **transitively** through sub-agents).
- Drop a modality word (for example "image") from the intent text **only when the payload is of that same type**. In "generate an image of a report" (text payload), "image" names the wanted output and must stay.
- No `if "pdf" in query` style rules anywhere. Thresholds and scoring weights are configuration.
- Make the embedder pluggable (a simple offline bag-of-words matcher by default; dense embeddings later).

**Explainability.** Before execution, show for every step: which agent, what it does, why it was chosen, how it helps, tools, and input to output. Store this on the `Execution` node and on the `INCLUDES` edges. Also keep a structured **execution trace** built from logged events (never from model-written self-explanations). Scrub it for secrets as it is written.

**Breakpoints.** Named reasons: `no_match`, `missing_input`, `permission_denied`, `max_retries`, `output_invalid`, `max_steps`, `timeout`, `token_budget`. Defaults: 3 retries per tool call, 8 steps, 30 s, 20k tokens, 1 alternative workflow. Hard stops (never retry): `permission_denied`, `missing_input`, `timeout`, `token_budget`, `no_match`.
- If a tool exhausts its retries, **do not run an alternative workflow that would call the same failing tool again**. Log `alternative_skipped`.
- On a final break: tell the user what went wrong and ask a clarifying question; file an `Escalation`; if the goal agent itself keeps failing, start an improvement proposal; if no agent matched, ask the LLM for a **draft** agent definition stored as data only (it can never be routed to).
- On the first success of a newly composed workflow, store it as the query type's active workflow so similar future queries reuse it. Reuse only when the new request's goal agent is the same as the stored one (overlapping words alone must not trigger reuse).

## 7. Feedback and self-improvement loop

1. **Feedback validation:** identified user only; rating +1 or -1; one per user per execution; rate limit per hour; a negative rating counts only with a real comment; user ids are salted-HMAC hashed; comments are scrubbed.
2. **Trigger:** a proposal starts only when N distinct users (default 3) report a problem on the same workflow, or an automatic regression test fails, or the goal agent hit a loop break.
3. **LLM rewrite:** ask the LLM to improve only `system_prompt` and `pipeline`; parse and validate against the manifest schema; create a `candidate` version. Guardrails in the repository: an LLM cannot create the first version of an agent, cannot change input/output modalities, cannot create a version as `stable`, and cannot introduce a HIGH-risk tool **directly or through a sub-agent**. Treat all LLM output as untrusted.
4. **Eval gate:** run current and candidate workflows on stored `Case`s (automatic tests). Pass only if accuracy strictly improves, no previously passing case regresses, and latency and token budgets hold. Accuracy is the objective; latency and cost are constraints. No cases means no automatic promotion (status `needs_cases`).
5. **Human approval:** only a named human can approve. Approval creates the new workflow and makes it the query type's active one. The old version is kept, and rollback is a re-point.
6. Exactly one open proposal per workflow at a time.

## 8. Safety and privacy

- Tools have risk tiers (low, medium, high). **HIGH-risk tools need an explicit authority grant in the call context, enforced in code** in the tool registry (not in a prompt), with an audit log of calls and denials.
- Scrub secrets and PII (API keys, passwords, emails, phone numbers, card, Aadhaar, PAN) before anything is stored (queries, outputs, traces, comments, plans). Be honest in the docs that regex scrubbing does not catch names and free-form personal data.
- Store only hashed user ids.

Seed agents (7): `text_summarizer`, `audio_transcriber` (chainable), `image_classifier`, `image_generator`, `file_analyzer`, `meeting_note_taker` (composite: transcriber then summarizer, via sub-agents), `email_sender` (HIGH-risk tool, for testing permissions).

## 9. Milestones (stop after each, show test results)

1. **Core:** manifests, `GraphStore` (+ in-memory + Neo4j + `docker-compose.yml`), repository with versioning rules, tool registry with risk tiers, privacy scrubber, seed agents. Tests for every rule in section 5.
2. **Router and runner:** routing from facts, chain insertion, runner with breakpoints and limits, trace, workflow memory and reuse, plan stored in the graph. Tests for each breakpoint and for routing.
3. **LangGraph engine and LangChain adapter:** the state graph, optional approval interrupt, Mermaid diagram, `LangChainLLM` adapter and `make_llm("provider:model")` factory. **A test must prove the LangGraph engine and a plain-Python engine give identical results** on the same scenarios, by sharing the retry policy code.
4. **Feedback loop:** everything in section 7, with tests for every rule, including an LLM output that is invalid JSON, uses an unknown tool, or smuggles in a HIGH-risk tool (all must fail safely).
5. **CLI:** `demo`, `ask`, `explain`, `agents`, `versions`, `feedback`, `case`, `proposals`, `approve`, `reject`, `catalog`, `cypher`, with graph persistence between commands (`--graph-file`, or Neo4j through env vars).
6. **Documentation** (section 10) and the final demo run.

## 10. Documentation to produce (Markdown, in `docs/`, with Mermaid diagrams)

`README.md` (what it is, quickstart, layout, honest status and limitations); `PROJECT_DESCRIPTION.md` (what it does, why useful, inputs, how a query is processed, workflow, outputs, what will be built next); `ARCHITECTURE.md` (components, data flow, sequence diagram, LangGraph diagram); `GRAPH_MODEL.md`; `VERSIONING.md` (with the status state machine); `ROUTER_AND_EXECUTION.md` (routing algorithm, breakpoint table, trace); `FEEDBACK_LOOP.md`; `SAFETY_AND_PRIVACY.md`; `EVALUATION.md`; `ROADMAP_AND_OPEN_QUESTIONS.md`; `AGENT_CATALOG.md` (generated from the graph); `DECISIONS.md` (every assumption you made where this prompt was ambiguous).

## 11. Acceptance: the demo must show, in order

summarize text; summarize an audio file (the transcriber is inserted automatically); classify an image; analyze a file; generate an image; high-risk tool denied, then allowed with a grant; no-match breakpoint (draft agent request filed); missing-input breakpoint; the same query type reused from memory; three distinct users report a problem, a candidate version is created and passes the eval gate; a human approves and the next query uses the new version; the old version is still stored and the lineage is shown; the same flow through LangGraph with a plan-approval pause.

## 12. Working rules

- Strict type hints, small modules, no dead code, no global state.
- Write a test for every invariant above. A rule that is not tested does not count as done.
- **Be honest.** Mark anything not verified (for example the Neo4j backend if you could not run a live server, any real LLM provider path, any stub tool) in the README and docs. Never claim it works if you did not run it.
- If a requirement is ambiguous, choose the safer default, record it in `DECISIONS.md`, and keep going. Do not silently expand scope.
- Do not add features beyond this prompt (no web UI yet; the CLI is enough for the prototype).