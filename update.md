# Update Log

Running log — one short entry appended after every prompt.

## 19-10-2026 — Prompt 1: continue project (pre-M5 closure brief)

Read-only exploration; **no code changed, no tests run**.

- Read `docs/MUTATION_CHECKS.md`, `tests/conftest.py`, `agent_repo/feedback.py`, `llm.py`,
  `tests/test_feedback.py`, `tests/test_cli.py`, `cli.py`, `pyproject.toml`, `.github/copilot-instructions.md`.
- Baseline collected 253 test cases (96 `[memory]`, 96 `[neo4j]`, 61 backend-independent).
- Verified Neo4j AuraDB reachable from `.env` (read-only count query succeeded).
- Assessed the four pre-M5 closure items — **none closed yet**:
  1. failing automatic case raises a proposal — test exists (`test_feedback.py:164`), no mutation evidence;
  2. rollback + default-version-unchanged — no `default_version` assertion in `test_feedback.py:375`;
  3. `approve()` refusing a `rejected_by_gate` proposal — untested (only `needs_cases` path at `:333`);
  4. `MOCK LLM: scripted rewrite` label — `feedback.py:365` stores `"MOCK LLM"`; only a hardcoded
     print at `cli.py:305` emits the full string, not the LLM path itself.
- Asked which fixture "sysm" refers to (no such fixture exists); question was dismissed —
  to be treated as the shared backend-parametrized `graph` fixture chain unless told otherwise.

## 19-10-2026 — Prompt 2: status report (read AGENTS.md + SPEC.md, no changes)

- `AGENTS.md` does not exist anywhere in the workspace; read `docs/SPEC.md` (reference only).
- Reported: I changed nothing today; M5 CLI code + 7 CLI tests exist (written by the earlier
  git-copilot session at 10:45–10:53) but have **no recorded test run**, and `MUTATION_CHECKS.md`
  is still M4-only (16-10). Current collection = 253 (table per file/backend delivered).
- The `244 passed` figure in `MUTATION_CHECKS.md:70` has **no recorded command**; documented
  full dual-backend command is `$env:NEO4J_TEST_WIPE="1"; .venv\Scripts\python.exe -m pytest -v -s`
  (`README.md:89-94`). 244 = M4-era collection 246 − 2 skips (inference, flagged).
- `MUTATION_CHECKS.md` marks **zero** requirements MISSING — all rows read "Covered".
- `test_feedback.py`: all 18 functions / 48 cases use the `graph` fixture (16 via
  `feedback_context`, 2 via `seeded`); **none are memory-only**.
- Stopped per instruction; no code changed, no tests run.

## 19-10-2026 — Prompt 3: AGENTS.md + M5 audit (read-only)

- Created `AGENTS.md` (rules from `.github/copilot-instructions.md` + safety/process rules).
- Corrected earlier total: current collection is **253 = 97 [memory] + 97 [neo4j] + 59
  backend-independent** (previous 96/96/61 row was an arithmetic error; per-file numbers were right).
- `NEO4J_TEST_WIPE` confirmed unset; ran only
  `.venv\Scripts\python.exe -m pytest tests\test_cli.py -k "not neo4j" -q` → **5 passed,
  2 deselected in 3.06s** (no skips/failures; no Neo4j connection). The two deselected are the
  `test_cli_agents_command_on_aura_graph` cases (by elimination — a run of the `[memory]` case
  would have reported a skip).
- M5 audit (no code changed): all 12 SPEC §9 M5 commands implemented and covered by
  `tests/test_cli.py`; demo follows SPEC §11 order; breakpoints are real conditions;
  no wipe/NEO4J_TEST_WIPE path in the CLI; credentials only exit via `_safe_text`
  (`cli.py:130-146`) except three raw demo prints of non-config content (`cli.py:304-305, 364`).
  Still open: `rewrite_label` is stored as `"MOCK LLM"` (`feedback.py:365`), not
  `MOCK LLM: scripted rewrite` (demo only hardcodes the print, `cli.py:305`).

## 19-10-2026 — Prompt 4: operating skills received

No task attached; adopted four working skills for the rest of the session (they live only
in chat, not on disk — `daily-note` arrived twice, treated as one):
- **safe-tests**: memory-only runs by default (`-k "not neo4j"`), prefer the single changed
  file, `NEO4J_TEST_WIPE` unset; any `[neo4j]` run needs permission first; never the full
  dual-backend suite outside milestone end; compact counts, failures only.
- **milestone-audit**: read-only, every claim backed by file/line/test, requirement table,
  name the test that fails per safety rule, end with gaps.
- **docs-from-code**: document only what the code does; catalog/Cypher generated from the
  CLI; state limits plainly.
- **daily-note**: 5 lines max (done with counts / next / blockers / risks / one decision).
No files changed except this log entry.

## 19-10-2026 — Prompt 5: three-item fix (label, _safe_text, demo tests)

- `feedback.py:365` now stores `"MOCK LLM: scripted rewrite"` when `llm.is_mock` (was
  `"MOCK LLM"`); `test_feedback.py:200` assertion updated to the exact new string.
- Demo no longer hardcodes the label: `cli.py:309-311` reads `rewrite_label` from the stored
  Proposal node after `generate_candidate` and prints it via `_print` → `_safe_text`; the
  section-header print at `cli.py:304` and the resumed LangGraph print at `cli.py:364` now
  also route through `_print` (all other demo prints left untouched per instruction).
- Two new memory-only tests in `tests/test_cli.py`:
  `test_demo_end_to_end_shows_stored_label_gate_rejection_and_real_breakpoint` (demo output
  contains stored label, `rejected_by_gate`, `Breakpoint: missing_input`, and the saved
  graph file's Proposal node carries the same `rewrite_label`), and
  `test_demo_redacts_a_secret_looking_string_from_demo_content` (a scrub-immune secret in a
  routed agent's stored purpose, present as a matching env var, never reaches demo stdout —
  `[REDACTED_CREDENTIAL]` proves redaction fired). The redaction test exposed one more raw
  print of `format_explanation` at `cli.py:363` (LangGraph pause section) — also routed
  through `_print` (one-line fix; only content-bearing raw print left after `:304-305, 364`).
- Verification (memory only, no Neo4j): `pytest tests\test_cli.py -k "not neo4j" -q` →
  **7 passed, 2 deselected in 3.76s**; `pytest tests\test_feedback.py -k "not neo4j" -q` →
  **24 passed, 24 deselected in 1.36s**. Docs (SPEC/DECISIONS/MUTATION_CHECKS) untouched.

## 19-10-2026 — Prompt 6: remaining raw prints + cypher stdout test

- Routed the last four raw demo/export prints through `_print` → `_safe_text`: `cli.py:313`
  (Candidate/evaluation), `cli.py:323` (Old version retained/lineage), `cli.py:353`
  (Useless rewrite evaluation), `cli.py:446` (cypher script to stdout). Behavior otherwise
  identical; `cli.py:441` keeps its `_safe_text` so the `--output` file path stays scrubbed
  (stdout path now double-scrubs — idempotent, same bytes).
- Demonstrated a scrub false positive on secret-free Cypher: a plain 10-digit reference
  `9876543210` in an `AgentRequest.description` exports as `[REDACTED_PHONE]` (phone rule,
  `privacy.py:51`). Documented, unchanged — redaction-over-inclusion is the chosen trade.
- New test `test_cypher_stdout_redacts_env_credential` (`tests/test_cli.py`): CLI `cypher`
  over `--graph-file` whose node props contain the `NEO4J_PASSWORD` env value → stdout has
  `[REDACTED_CREDENTIAL]` and not the raw value.
- Memory-only run: `pytest tests\test_cli.py -k "not neo4j" -q` → **8 passed, 2 deselected
  in 3.03s**. No Neo4j run; docs untouched.

## 19-10-2026 — Prompt 7: read-only redaction scan of demo graph export

- No code changed. Demo + `cypher --output` run to temp files (exit 0/0). Export (101,638 B)
  contains **8× [REDACTED_AADHAAR]** (export-time false positives: 12-digit runs inside hex
  `content_hash`/`id` — Aadhaar lookbehind is `(?<!\d)`, so hex-letter-preceded runs match)
  and **13× [REDACTED_SECRET]** (pre-redacted placeholders: storage-time `scrub_obj` redacts
  `tokens_used` values because the key contains substring "token", privacy.py:16,68). Phone/
  email/card/PAN/API-key: 0.
- Phone rule (privacy.py:51) vs 613 raw stored props (65 nodes, 74 rels): **0 matches**.
  Timestamps max digit-run 6 (isoformat), counters 1, content_hash 12 / id 9 but mid-string
  (blocked by `(?<![\w+])`); would match at value start or dash-grouped (e.g. "9876543210",
  "3141592653abcdef0").

## 19-10-2026 — Prompt 8: three redaction fixes + tests

- Fix 1 `privacy.py:49-59`: card/Aadhaar/PAN/phone rules now require non-word boundaries on
  both sides (`(?<!\w)` / `(?!\w)`; phone keeps `(?<![\w+])`) — no matches inside hex ids or
  content hashes. Real spaced/dash formats still redact (existing parametrized cases kept).
- Fix 2 `cli.py`: cypher export (stdout + `--output`) now uses `_redact_configured_secrets`
  (exact env/settings values only, no pattern scrub); `_safe_text` refactored over a shared
  `_configured_secrets` helper and keeps pattern scrub for all other output.
- Fix 3 `privacy.py:16-20`: `_SENSITIVE_KEYS` is segment-aware — password/pwd/passphrase/
  secret/api_key/api_token/access_token/auth_token/refresh_token/authorization/credential/
  bearer + bare `token`; metric keys (`tokens_used`, `max_tokens`, `token_budget`, `*_tokens`)
  stay numeric. Trace `tokens_used` now stored as int (runner.py:54 scrub_obj path).
- New tests: 33 in `tests/test_privacy.py` (boundary, standalone-format, metric-key,
  secret-key) + 2 in `tests/test_cli.py` (demo export verbatim, trace tokens int ==
  runner count on the Execution node). Mutation checks: each fix reverted → its new test
  failed (privacy 2/5 cases; export zero-REDACTED; tokens isinstance-int) → restored.
- Read-back audit (#4): no code reads `trace_json`, `evaluation_json`, or
  `last_tokens_used` from the graph — all `tokens_used` logic uses in-memory values; the
  old redaction never affected logic, only stored/exported text.
- Full memory-only run: `pytest -q -k "not neo4j"` → **170 passed, 121 deselected in
  5.62s**. No Neo4j run.

## 19-10-2026 — Prompt 10: why -k neo4j over-matches (read-only)

- No code changed. Collection probe printed keywords for the 10 listed tests:
  the 9 graph-fixture tests match `-k neo4j` solely via the **marker keyword
  `neo4j_smoke`** (`@pytest.mark.neo4j_smoke` at test_graph_store.py:22/35/40,
  test_routing_and_runner.py:26/110/165, test_langgraph_and_llm.py:93,
  test_versioning.py:30/53 — pytest `-k` matches marker names, substring, case-insensitive);
  `test_secret_keys_are_still_redacted[NEO4J_PASSWORD]` matches via its **parametrize ID**
  (proved: `-k neo4j tests/test_privacy.py` collects exactly that 1 case).
- `-m` matches exact marker names only (proved: `-m neo4j` → 0 collected/291 deselected while
  `neo4j_smoke` exists) → proposed conftest `pytest.param("neo4j", marks=pytest.mark.neo4j)`
  + `-m "not neo4j"` selects 97 memory + 97 independent = 194 (deselects 97).
- Counts: memory=97, neo4j=97, independent=97, total=291. Current `-k "not neo4j"` wrongly
  drops 24 items (21 [memory]-param + 3 independent) besides the 97 [neo4j].

## 19-10-2026 — Prompt 11: apply selector fix (mark neo4j param) + verify; docs selector/counts

- `tests/conftest.py:92-95`: `graph` fixture now `params=[pytest.param("memory", id="memory"),
  pytest.param("neo4j", id="neo4j", marks=pytest.mark.neo4j)]`; ids unchanged so nodeids are stable.
- `pyproject.toml` `[tool.pytest.ini_options]`: `--strict-markers` is **not** set (`addopts = "-ra"`);
  `neo4j_smoke` was already registered, added the new `neo4j` marker line. Nothing else changed.
- Verification (collection only, no Neo4j connection, `NEO4J_TEST_WIPE` unset):
  total **291**; `-m neo4j` → **97** collected (all 97 ids contain `neo4j`; **0** without);
  `-m "not neo4j"` → **194** collected (0 selected ids carry the `[neo4j]` backend; 3 selected
  ids merely *mention* neo4j in their names — `test_no_neo4j_without_uri`,
  `test_validate_neo4j_uri_rejects_missing_and_placeholders`,
  `test_secret_keys_are_still_redacted[NEO4J_PASSWORD]`). Marks cross-tab (probe over callspec):
  marked=97, marked_but_memory=0, marked_but_independent=0, neo4j_param_without_mark=0 →
  every test deselected by `-m "not neo4j"` is a neo4j-backend instance; zero memory/independent dropped.
- Run `pytest -q -m "not neo4j" | Select-Object -Last 12` → **193 passed, 1 skipped, 97 deselected
  in 6.03s**. Not "194 passed": `tests/test_cli.py:287` `test_cli_agents_command_on_aura_graph[memory]`
  is now *selected* (it was previously hidden from `-k` runs by its `neo4j_smoke` marker keyword) and
  self-skips by design ("Aura CLI smoke test runs only against the Neo4j backend."). 194 selected =
  193 pass + 1 designed skip; no failures.
- Docs: `README.md:79` selector `-k "not [neo4j]"` → `-m "not neo4j"`. `AGENTS.md` contains no
  `-k`/selector at all (nothing to replace). `docs/MUTATION_CHECKS.md:60-65` stale present-tense
  paragraph "Current collection is 246 cases: 96/96/54 … 150 before skips … 24 more" corrected to
  **291 = 97/97/97, 194 memory-side before skips, +68 vs the M3 figure**. Lines 51-58 (historical
  `-k memory` run outputs 96 passed/149 deselected/54) and 67-70 (M4 runs) left untouched as run
  records. `-k memory` at `MUTATION_CHECKS.md:52` is not the `not neo4j` selector — left as-is.

## 19-10-2026 — Prompt 12: authorized full dual-backend run (AuraDB wiped)

- Shell-only `$env:NEO4J_TEST_WIPE="1"` (unset in `finally`), command as given:
  `pytest -q --durations=15 | Select-Object -Last 45` → **290 passed, 1 skipped in 767.17s
  (12m47s)**, no failures, no errors. 291 collected → 290 + 1 skip.
- Only skip: `tests\test_cli.py:287` `test_cli_agents_command_on_aura_graph[memory]` —
  "Aura CLI smoke test runs only against the Neo4j backend." (by design; the `[neo4j]`
  instance ran). No other skips, no LLM-provider skips, no warnings-as-errors.
- Slowest: 55.63s `test_runtime_breakpoints_retries_alternative_skipped_and_limits[neo4j]`,
  35.00s `test_real_breakpoints_no_match_missing_input_and_permission_denied[neo4j]`,
  34.18s `test_workflow_memory_reuse_and_execution_subgraph[neo4j]`, 27.39s setup
  `test_eval_gate_requires_strict_accuracy_improvement_without_regression[neo4j]`,
  21.43s + 21.42s + 21.02s + 21.00s + 19.87s (routing/feedback `[neo4j]`), 12.24s,
  12.21s, 10.49s `test_langgraph_approval_interrupt_pause_and_resume[neo4j]`, 10.20s,
  8.59s, 8.43s setup — all 15 are `[neo4j]` (AuraDB latency + per-test wipe).
- Resolves the prompt-9 empty output: that command had the default ~120s tool timeout while
  the real suite takes ~768s, so the run was killed before any output (inference; prompt 9
  produced no counts and is not otherwise recorded). No code changed in this prompt.

## Next up (from the assigned brief, still open)

Close the four items with tests that fail under mutation, list/verify backend parametrization of
the feedback/eval selection, move eligible memory-only tests onto the shared fixture where it
makes sense, update `docs/MUTATION_CHECKS.md` (no MISSING left), run both backends, report counts
per file per backend, then STOP.
