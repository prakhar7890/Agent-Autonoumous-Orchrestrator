# Graph Report - Agent Autonomous Orcrastrator  (2026-10-16)

## Corpus Check
- Corpus is ~19,486 words - fits in a single context window. You may not need a graph.

## Summary
- 577 nodes · 1914 edges · 19 communities (14 shown, 5 thin omitted)
- Extraction: 78% EXTRACTED · 22% INFERRED · 0% AMBIGUOUS · INFERRED: 412 edges (avg confidence: 0.91)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- LangGraph & Plain Execution Engines
- Neo4j Cloud Graph Store
- In-Memory Graph Store
- Engine Equivalence & LLM Tests
- Execution Runner & Breakpoint Policy
- Milestone 1 Repository & Guardrail Tests
- Router, Fact Extraction & Workflow Memory
- Agent Repository & Lineage Service
- LLM Providers & LangChain Adapter
- Tool Registry & Authority Grants
- Milestone 2 Routing & Breakpoint Tests
- Agent Manifest & Content Hashing
- PII Scrubber & Salted User Hashing
- Execution Subgraph Views & Cypher Export
- Stub & Real Tool Implementations

## God Nodes (most connected - your core abstractions)
1. `AgentRepository` - 96 edges
2. `Modality` - 61 edges
3. `Runner` - 43 edges
4. `Payload` - 42 edges
5. `AgentManifest` - 34 edges
6. `CallContext` - 34 edges
7. `ToolRegistry` - 30 edges
8. `Neo4jGraph` - 28 edges
9. `scrub()` - 28 edges
10. `Node` - 25 edges

## Surprising Connections (you probably didn't know these)
- `_normalize_result()` --uses--> `ExecutionResult`  [INFERRED]
  tests/test_langgraph_and_llm.py → agent_repo/execution/runner.py
- `test_real_breakpoints_no_match_missing_input_and_permission_denied()` --uses--> `Runner`  [INFERRED]
  tests/test_routing_and_runner.py → agent_repo/execution/runner.py
- `test_workflow_memory_reuse_and_execution_subgraph()` --uses--> `Runner`  [INFERRED]
  tests/test_routing_and_runner.py → agent_repo/execution/runner.py
- `graph()` --uses--> `Neo4jGraph`  [INFERRED]
  tests/conftest.py → agent_repo/graph/neo4j_store.py
- `_neo4j_session_store()` --uses--> `Neo4jGraph`  [INFERRED]
  tests/conftest.py → agent_repo/graph/neo4j_store.py

## Import Cycles
- None detected.

## Communities (19 total, 5 thin omitted)

### Community 0 - "LangGraph & Plain Execution Engines"
Cohesion: 0.06
Nodes (33): ExecutionLimits, EngineState, LangGraphEngine, node_approval(), node_attempt(), node_finish_broken(), node_finish_declined(), node_learn() (+25 more)

### Community 1 - "Neo4j Cloud Graph Store"
Cohesion: 0.08
Nodes (48): is_human_name(), NotHumanError, require_human(), workflow_reachable_tool_names(), Status, AgentRepository, AgentVersionRecord, GuardrailError (+40 more)

### Community 2 - "In-Memory Graph Store"
Cohesion: 0.08
Nodes (43): Modality, RiskTier, audio_transcribe_stub(), AuditEntry, AuthorityGrant, builtin_registry(), CallContext, email_send_stub() (+35 more)

### Community 3 - "Engine Equivalence & LLM Tests"
Cohesion: 0.05
Nodes (9): LangChainLLM, LLMError, LLMProvider, LLMResponse, make_llm(), _merged_env(), MockLLM, _FakeLangChainChatModel (+1 more)

### Community 4 - "Execution Runner & Breakpoint Policy"
Cohesion: 0.08
Nodes (15): AgentManifest, PipelineStep, AgentDescription, _hash_text(), IntegrityIssue, _agent(), _tool(), _base() (+7 more)

### Community 5 - "Milestone 1 Repository & Guardrail Tests"
Cohesion: 0.09
Nodes (10): RouterConfig, BagOfWordsEmbedder, Embedder, extract_facts(), infer_payload_modality(), RequestFacts, RouteOutcome, Router (+2 more)

### Community 6 - "Router, Fact Extraction & Workflow Memory"
Cohesion: 0.15
Nodes (5): Neo4jGraph, _tx(), _node(), _rel(), check_props()

### Community 7 - "Agent Repository & Lineage Service"
Cohesion: 0.13
Nodes (12): _card(), _luhn_ok(), scrub(), scrub_obj(), UserIdHasher, test_hasher_refuses_empty_user(), test_hasher_refuses_weak_salt(), test_hashing_is_salted_and_deterministic() (+4 more)

### Community 8 - "LLM Providers & LangChain Adapter"
Cohesion: 0.13
Nodes (7): Neo4jSettings, Neo4jConnectionError, _translate_driver_error(), validate_neo4j_uri(), check_mutation(), new_id(), test_validate_neo4j_uri_rejects_missing_and_placeholders()

### Community 9 - "Tool Registry & Authority Grants"
Cohesion: 0.19
Nodes (6): GraphStore, Rel, test_crud_and_rel_queries(), test_immutable_props_refused_by_store(), test_schema_is_enforced(), test_store_has_no_delete_operation()

### Community 10 - "Milestone 2 Routing & Breakpoint Tests"
Cohesion: 0.19
Nodes (6): check_label(), check_rel_type(), InMemoryGraph, Node, tamper(), test_json_roundtrip()

### Community 11 - "Agent Manifest & Content Hashing"
Cohesion: 0.18
Nodes (10): _cypher_literal(), _cypher_props(), execution_subgraph(), ExecutionSubgraph, export_cypher(), render_execution_subgraph(), SubgraphAgentStep, test_real_breakpoints_no_match_missing_input_and_permission_denied() (+2 more)

### Community 12 - "PII Scrubber & Salted User Hashing"
Cohesion: 0.18
Nodes (7): Settings, _env(), graph(), _neo4j_session_store(), repo(), test_env_overrides_file(), test_no_neo4j_without_uri()

### Community 13 - "Execution Subgraph Views & Cypher Export"
Cohesion: 0.29
Nodes (3): GraphError, wipe_neo4j_for_tests(), _wipe_tx()

## Knowledge Gaps
- **1 isolated node(s):** `agent-repo`
  These have ≤1 connection - possible missing edges. (Counts symbols only; 161 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **5 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `AgentRepository` connect `Neo4j Cloud Graph Store` to `LangGraph & Plain Execution Engines`, `In-Memory Graph Store`, `Execution Runner & Breakpoint Policy`, `Milestone 1 Repository & Guardrail Tests`, `Tool Registry & Authority Grants`, `Milestone 2 Routing & Breakpoint Tests`, `Agent Manifest & Content Hashing`, `PII Scrubber & Salted User Hashing`?**
  _High betweenness centrality (0.191) - this node is a cross-community bridge._
- **Are the 55 inferred relationships involving `AgentRepository` (e.g. with `Runner` and `select_next_alternative()`) actually correct?**
  _`AgentRepository` has 55 INFERRED edges - model-reasoned connections that need verification._
- **What connects `agent-repo` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `LangGraph & Plain Execution Engines` be split into smaller, more focused modules?**
  _Cohesion score 0.060246360582306833 - nodes in this community are weakly interconnected._
- **Why does `Modality` connect `In-Memory Graph Store` to `LangGraph & Plain Execution Engines`, `Neo4j Cloud Graph Store`, `Engine Equivalence & LLM Tests`, `Execution Runner & Breakpoint Policy`, `Milestone 1 Repository & Guardrail Tests`, `Agent Manifest & Content Hashing`?**
  _High betweenness centrality (0.113) - this node is a cross-community bridge._
- **Are the 38 inferred relationships involving `Modality` (e.g. with `EngineState` and `node_attempt()`) actually correct?**
  _`Modality` has 38 INFERRED edges - model-reasoned connections that need verification._
- **Should `Neo4j Cloud Graph Store` be split into smaller, more focused modules?**
  _Cohesion score 0.08439450686641697 - nodes in this community are weakly interconnected._