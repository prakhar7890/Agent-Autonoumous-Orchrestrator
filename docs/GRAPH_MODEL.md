# Graph Model

The graph is the source of truth for agents, versions, tools, workflows, executions, feedback and
lineage. Both backends enforce the same node labels, relationship types and property rules
(`agent_repo/graph/store.py`). Large payloads (manifests, traces, plans, case JSON) are stored as
JSON **string** properties; graph structure carries lineage and relationships.

## Node labels

| Label | Purpose | Notable properties |
| --- | --- | --- |
| `Agent` | Identity of one agent (task type). | `name`, `created_by`, `created_at`, `default_version` |
| `AgentVersion` | One immutable version (manifest). | `agent`, `version` (`0.N`), `version_seq`, `status`, `author`, `manifest_json`, `content_hash`, `created_at`, `change_note`, `purpose`, `provider`, `model` |
| `Tool` | A callable tool with a risk tier. | `name`, `description`, `risk`, `is_stub`, `input_modalities`, `output_modality` |
| `QueryType` | A `payload->goal_agent` key with an active workflow. | `key`, `payload_modality`, `goal_agent`, `created_at` |
| `Workflow` | An ordered composition of agent versions for a query type. | `query_type_key`, `goal_agent`, `goal_version`, `steps_json`, `active`, `created_at` |
| `Execution` | One run. | `query`, `query_type_key`, `status`, `plan_text`, `plan_json`, `trace_json`, `output_text`, `break_reason`, `steps_taken`, `tokens_used`, `elapsed_seconds`, `created_at` |
| `Feedback` | One user rating of an execution. | `execution_id`, `workflow_id`, `user_hash`, `rating`, `comment`, `created_at` |
| `Case` | An automatic evaluation case. | `workflow_id`, `name`, `case_json`, last-run fields |
| `Proposal` | An improvement proposal for a workflow. | `workflow_id`, `agent`, `base_version`, `trigger`, `status`, `candidate_version`, `rewrite_label`, `evaluation_json` |
| `Escalation` | Filed on every final break. | `execution_id`, `reason`, `detail`, `clarifying_question` |
| `AgentRequest` | A non-routable draft agent definition (on `no_match`). | `query`, `payload_modality`, `status` (`draft`), `routable` (`false`), `draft_json` |

## Relationship types

| Relationship | From → To | Notes |
| --- | --- | --- |
| `HAS_VERSION` | `Agent → AgentVersion` | |
| `SUPERSEDES` | `AgentVersion → AgentVersion` | new version points at its parent (lineage chain) |
| `USES_TOOL` | `AgentVersion → Tool` | `order` stored on the edge |
| `DELEGATES_TO` | `AgentVersion → AgentVersion` | sub-agent steps are **version-pinned**; `order` on the edge |
| `PREFERS` | `QueryType → Workflow` | active workflow; `active`, `since` on the edge |
| `INCLUDES` | `Workflow → AgentVersion` | `order`, `role` (`prerequisite`/`goal`) and the stored `reason` on the edge |
| `RAN` | `Execution → Workflow` | |
| `OF_TYPE` | `Execution → QueryType` | |
| `GOT` | `Feedback → Execution` | |
| `HAS_CASE` | `Workflow → Case` | |
| `HAS_PROPOSAL` | `Workflow → Proposal` | at most one *open* proposal per workflow |
| `PROPOSES` | `Proposal → AgentVersion` | the candidate version created for the proposal (`generate_candidate`) |
| `ESCALATED` | `Execution → Escalation` | |
| `REQUESTED` | `Execution → AgentRequest` | |

## Schema and immutability rules

- Node labels and relationship types are validated against fixed whitelists; unknown ones raise
  `GraphError`.
- Property values must be scalars or homogeneous lists of scalars; nested values/dicts must be stored
  as JSON strings.
- The `AgentVersion` properties `manifest_json`, `content_hash`, `agent`, `version`, `version_seq`,
  `author` and `created_at` are **immutable** once written.
- **There is no delete operation on `GraphStore`.** "Delete" means the `deprecated` status, so old
  versions remain queryable (version 0.1 may handle queries 0.2 does not).
- On Neo4j every node also carries a helper label `GraphNode` with a unique `id` constraint, so
  lookups by id use an index. `Neo4jGraph` exposes no wipe/delete method; test wiping lives only in
  `tests/conftest.py`.

## Views and export

- `execution_subgraph(execution_id)` returns the Execution, its QueryType and Workflow, each
  `AgentVersion` with the **stored reason** from the `INCLUDES` edge, and every reachable tool
  including the tools behind sub-agents. `render_execution_subgraph()` renders it as text
  (`agent-repo explain EXECUTION_ID`).
- `export_cypher(graph)` emits a loadable script (`agent-repo cypher [--output PATH]`); the CLI
  redacts the exact configured credential values before writing. See
  [`graph_export.cypher`](graph_export.cypher).
- [`queries.cypher`](queries.cypher) holds named Cypher queries: version lineage, which agents use a
  tool (directly or transitively), the active workflow per query type, and recent executions.
