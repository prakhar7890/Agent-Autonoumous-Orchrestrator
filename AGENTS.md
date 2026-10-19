# AGENTS.md — rules for working in this repository

These rules are binding for every session. They extend `.github/copilot-instructions.md`.

## Non-negotiable safety rules

1. **Credentials live only in `.env`.** Never print, log, echo, or copy them into any file,
   test fixture, doc, or command output. Never edit `.env`. `.env.example` is the template.
2. **`NEO4J_TEST_WIPE=1` is set only from the shell, for one run.** It is never read from
   `.env` (`tests/conftest.py` strips it) and never set programmatically.
3. **Any command that wipes or writes to AuraDB/Neo4j needs my explicit permission, each
   time** — prior approval does not carry over. Read-only queries against AuraDB are allowed
   when clearly described beforehand.
4. **Never `git push`, never delete files, never edit `.env`.** No force-adds, no history rewrites.
5. **Do not run tests unless I ask.** When I do ask, run exactly the command specified.

## Process rules

6. **`docs/SPEC.md` is reference only** — never edit it; it is the original requirements text.
7. **One milestone at a time, then STOP.** Show real test results at the end of each milestone
   and wait for my go-ahead before starting the next one.
8. **Report real counts** (per file, per backend: `[memory]` / `[neo4j]` / backend-independent)
   taken from an actual run, never estimated. Anything not executed in this session must be
   labelled **"not verified"**. Be honest about stubs, unrun backends, and unrun LLM providers.
9. **No scope expansion.** No new features beyond the current milestone; record ambiguous
   decisions in `docs/DECISIONS.md` instead of silently choosing.

## Project invariants (from `.github/copilot-instructions.md`)

- Manifest versions are immutable; nothing is hard-deleted; only a named human can promote a
  version to `stable`; machine authors (`llm*`, `system*`) cannot.
- LLM output is untrusted. An LLM may not create an agent's first version, change I/O
  modalities, create a stable version, or reach a HIGH-risk tool directly or through a sub-agent.
- HIGH-risk tools need an explicit authority grant, enforced in code (never only in a prompt).
- No hardcoded per-query routing and no intent-to-output table.
- A rule that is not covered by a test does not count as done.
