# Binding Decisions

These decisions resolve implementation details for the project and are binding for
the prototype. They supplement the original requirements in [SPEC.md](./SPEC.md).

## Runtime and graph backend

- Use a Python 3.12 virtual environment for development and verification.
- Neo4j AuraDB Free is the verified graph backend. `deploy/optional/docker-compose.yml`
  is an unverified, optional local alternative and must not be described as verified.

## Cases and breakpoints

- Automatic cases support both exact-field checks and “must not contain” checks.
- Feedback is limited to 10 submissions per identified user in a rolling hour by
  default; the limit is configurable on the feedback service.
- Negative feedback requires a substantive comment of at least 8 characters; common
  placeholder comments are rejected.
- Demo breakpoints must be produced by real runtime conditions; do not fabricate
  breakpoint results solely for demonstration.

## Routing and evaluation

- Select a workflow's goal agent only by scoring request terms against stored agent
  metadata. Do not add an intent-to-output table or hardcoded per-query routing.
- A candidate passes evaluation only when accuracy strictly improves, no previously
  passing case regresses, and latency and token budgets hold.
- With no evaluation cases, mark the proposal `needs_cases`; never auto-promote it.

## LLM rewrite and demonstration

- The demo includes a scripted rewrite explicitly labelled `MOCK LLM`.
- The demo also includes one rewrite rejected by the evaluation gate.
- Treat every rewrite as untrusted input; it may change only `system_prompt` and
  `pipeline`, and must be validated before a candidate version is created.

## Governance and command-line interface

- “Human approver” is a naming convention, not authentication.
- Use Python's `argparse` only for command-line parsing; do not add another CLI
  framework.
- The M5 CLI uses `--graph-file` for JSON-persisted memory storage; otherwise it
  uses Aura when `NEO4J_URI` is configured, and ephemeral memory when it is not.
- CLI rewrites use the deterministic `MockLLM` offline by default. A proposal
  rewrite can be supplied explicitly as JSON; no credential or test-wipe option
  is exposed in the CLI.
- The ordered demo writes to the selected graph. Use a fresh graph file for a
  repeatable clean run; audio, image, image-generation, and email integrations
  remain labelled deterministic stubs.
