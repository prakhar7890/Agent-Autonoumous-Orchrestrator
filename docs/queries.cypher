// ============================================================================
// docs/queries.cypher — Useful Cypher queries for Neo4j AuraDB / Neo4j Browser
// Each query is prefixed with a `-- @name: <name>` marker so automated runners
// and humans can execute and inspect them individually.
// ============================================================================

// -- @name: agent_lineage
// 1. Version lineage (SUPERSEDES chain) for every agent in the repository
MATCH (a:Agent)-[:HAS_VERSION]->(v:AgentVersion)
OPTIONAL MATCH (v)-[:SUPERSEDES]->(parent:AgentVersion)
RETURN a.name AS agent,
       v.version AS version,
       v.status AS status,
       v.author AS author,
       parent.version AS supersedes_version,
       a.default_version AS is_default_if_equal
ORDER BY agent, v.version_seq DESC;

// -- @name: agents_using_tool_transitively
// 2. Which agent versions use each tool (directly OR transitively via DELEGATES_TO sub-agents)
MATCH (v:AgentVersion)-[:DELEGATES_TO*0..4]->(leaf:AgentVersion)-[:USES_TOOL]->(t:Tool)
RETURN t.name AS tool,
       t.risk AS risk_tier,
       t.is_stub AS is_stub,
       v.agent AS caller_agent,
       v.version AS caller_version,
       leaf.agent + '@' + leaf.version AS tool_owner
ORDER BY risk_tier DESC, tool, caller_agent, caller_version;

// -- @name: active_workflow_per_query_type
// 3. Active workflow per QueryType, with ordered agent versions and stored INCLUDES reasons
MATCH (qt:QueryType)-[:PREFERS]->(w:Workflow)-[inc:INCLUDES]->(v:AgentVersion)
RETURN qt.key AS query_type,
       w.id AS workflow_id,
       inc.order AS step_order,
       inc.role AS role,
       v.agent + '@' + v.version AS agent_version,
       inc.reason AS stored_reason
ORDER BY query_type, step_order ASC;

// -- @name: recent_executions_and_outcomes
// 4. Recent executions with their QueryType, Workflow, Escalation, and draft AgentRequest
MATCH (e:Execution)
OPTIONAL MATCH (e)-[:OF_TYPE]->(qt:QueryType)
OPTIONAL MATCH (e)-[:RAN]->(w:Workflow)
OPTIONAL MATCH (e)-[:ESCALATED]->(esc:Escalation)
OPTIONAL MATCH (e)-[:REQUESTED]->(req:AgentRequest)
RETURN e.query AS query,
       e.status AS status,
       e.break_reason AS break_reason,
       qt.key AS query_type,
       w.goal_agent + '@' + w.goal_version AS workflow_goal,
       esc.reason AS escalation_reason,
       req.status AS draft_request_status
ORDER BY e.created_at ASC;

