# M4 Traceability and Mutation Checks

Review evidence for the M4 feedback loop. Test names below are pytest test
function names; parameterized tests run once per listed input/backend.

## Requirement traceability

| M4 requirement | Covering tests | Assessment |
|---|---|---|
| Identified user, rating restricted to `+1`/`-1`, and substantive comment for negative feedback | `test_feedback_requires_identified_user_valid_rating_and_negative_comment` | Covered |
| One feedback per user per execution; user IDs hashed; comments scrubbed | `test_feedback_is_hashed_scrubbed_and_one_per_user_per_execution` | Covered |
| Hourly feedback rate limit | `test_hourly_feedback_limit_is_enforced_per_hashed_user` | Covered |
| Trigger after N distinct users on one workflow | `test_negative_feedback_from_distinct_users_opens_one_proposal` | Covered |
| Trigger on a failing automatic case | `test_failing_automatic_case_triggers_one_proposal` | Covered |
| Trigger on a goal-agent loop break | `test_goal_agent_loop_break_opens_proposal_and_deduplicates` | Covered |
| At most one open proposal per workflow | `test_negative_feedback_from_distinct_users_opens_one_proposal`; `test_failing_automatic_case_triggers_one_proposal`; `test_goal_agent_loop_break_opens_proposal_and_deduplicates` | Covered by repeated/sequential triggers; concurrency is not tested |
| Rewrite changes only `system_prompt` and `pipeline` | `test_candidate_rewrite_is_labelled_mock_and_changes_only_allowed_fields` | Covered; test compares the complete candidate manifest against the base with only these fields replaced |
| LLM cannot create an agent's first version | `test_non_human_cannot_create_first_version` | Covered at repository guard; an M4 rewrite requires an existing base version |
| LLM cannot change input/output modalities | `test_llm_cannot_change_modalities`; `test_candidate_rewrite_is_labelled_mock_and_changes_only_allowed_fields` | Covered |
| LLM cannot create/promote a stable version | `test_new_versions_always_start_as_candidate`; `test_only_named_humans_promote_to_stable`; `test_candidate_rewrite_is_labelled_mock_and_changes_only_allowed_fields` | Covered |
| LLM cannot add a HIGH-risk tool directly or through a sub-agent | `test_rewrite_cannot_add_unknown_or_high_risk_tool_directly_or_transitively`; `test_llm_cannot_add_high_risk_tool_directly`; `test_llm_cannot_add_high_risk_tool_via_sub_agent` | Covered |
| Invalid JSON and malformed rewrite shapes fail without creating a candidate | `test_invalid_rewrite_shapes_fail_safely` | Covered; includes invalid JSON and schema/shape cases |
| Unknown-tool rewrite fails without creating a candidate | `test_rewrite_cannot_add_unknown_or_high_risk_tool_directly_or_transitively` | Covered |
| Accuracy must strictly improve, without regression on a previously passing case | `test_eval_gate_requires_strict_accuracy_improvement_without_regression`; `test_eval_gate_rejects_useless_mock_rewrite`; `test_eval_gate_rejects_regression_even_when_accuracy_improves` | Covered |
| Latency and token budgets must hold; no cases means `needs_cases` | `test_eval_gate_enforces_latency_and_token_budgets`; `test_no_cases_means_needs_cases_and_never_approves` | Covered |
| Only a named human may approve or reject | `test_human_approval_repoints_query_workflow_without_changing_agent_default`; `test_human_rejection_rejects_candidate_and_keeps_old_workflow` | Covered; approval checks both `llm:` and `system:` identities |
| Approval re-points the query type's `PREFERS` workflow | `test_human_approval_repoints_query_workflow_without_changing_agent_default` | Covered; also routes a query through the new workflow |
| Agent default version remains unchanged after approval | `test_human_approval_repoints_query_workflow_without_changing_agent_default` | Covered |
| Rollback re-points to the prior workflow | `test_rollback_repoints_to_prior_workflow_and_requires_human` | Covered |
| Scripted rewrite is labelled `MOCK LLM` in the demonstration | `test_candidate_rewrite_is_labelled_mock_and_changes_only_allowed_fields` | Covered as a scripted test-level demonstration; there is no separate runnable demo entrypoint in M4 |
| A useless rewrite is rejected by the gate | `test_eval_gate_rejects_useless_mock_rewrite` | Covered |

No M4 requirement in the requested list has no test coverage. The mock rewrite
demonstration is represented by the scripted `MockLLM` test rather than a
standalone CLI demo; CLI work remains outside M4.

## Mutation results

Each mutation was temporary, followed by a focused memory-backend pytest run.
All five tests failed under the corresponding mutation. The implementation was
restored after each check.

| Rule | Temporary mutation | Failing test |
|---|---|---|
| Evaluation gate rejects regressions | Removed the `not regressions` requirement from the gate's pass condition | `test_eval_gate_rejects_regression_even_when_accuracy_improves[memory]` — returned `pending_approval` instead of `rejected_by_gate` |
| Distinct-user threshold is enforced | Replaced the `len(hashes) >= distinct_user_threshold` check with a truthiness check | `test_negative_feedback_from_distinct_users_opens_one_proposal[memory]` — opened a proposal after the first user's feedback |
| Approval is human-only | Bypassed the service-level approver check and supplied a fixed human identity to the version repository | `test_human_approval_repoints_query_workflow_without_changing_agent_default[memory-llm:reviewer]` and `[memory-system:reviewer]` — both failed because no `NotHumanError` was raised |
| Rewrite creates a candidate, not a stable version | Promoted the new version through pending approval to stable inside candidate generation | `test_candidate_rewrite_is_labelled_mock_and_changes_only_allowed_fields[memory]` — observed `stable`, expected `candidate` |
| Approval leaves the agent default unchanged | Added `set_default_version` for the candidate during proposal approval | `test_human_approval_repoints_query_workflow_without_changing_agent_default[memory-llm:reviewer]` and `[memory-system:reviewer]` — approval raised the default-change guard |

The earlier result **96 passed, 1 skipped** came from
`.venv\Scripts\python.exe -m pytest -q -k memory`. This is a name filter, not
the complete memory-backend suite: it selects the 96 parametrized `[memory]`
cases, plus the `[neo4j]` `test_workflow_memory_reuse_and_execution_subgraph`
case because its test name contains “memory”; that Neo4j case skips without
`NEO4J_TEST_WIPE`. It deselects the 54 backend-independent collected cases.
The clean post-restoration run of that same command was **96 passed, 1
skipped, 149 deselected**.

Current collection is **291 cases**: 97 `[memory]`, 97 `[neo4j]`, and 97
backend-independent cases. Therefore the current full memory-side collection
is 194 cases before skips (97 memory-parametrized plus 97 shared), not the
filtered 96 result. The old M3 figure of 126 per backend is not comparable to
that filtered figure; the memory-side collection now exceeds it by 68 cases,
with the feedback tests accounting for 24 of them.

The newly authorized focused Neo4j run completed:
`.venv\Scripts\python.exe -m pytest tests\test_feedback.py -q` with
`NEO4J_TEST_WIPE=1` set in the shell environment → **48 passed** (24 memory,
24 Neo4j). The preceding M4 full run passed **244 tests** across both backends.
