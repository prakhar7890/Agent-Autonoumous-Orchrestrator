# Project rules

- Manifest versions are immutable; nothing is hard-deleted; only a named human can promote to stable; machine authors (`llm*`, `system*`) cannot.
- LLM output is untrusted. An LLM may not create an agent's first version, change I/O modalities, create a stable version, or reach a HIGH-risk tool directly or through a sub-agent.
- HIGH-risk tools need an explicit authority grant, enforced in code.
- No hardcoded per-query routing and no intent-to-output table.
- Credentials live only in `.env`. Never print or log them. Never read `.env` into outputs.
- `NEO4J_TEST_WIPE=1` comes only from the shell, never from `.env`. It deletes everything in the Aura database.
- Be honest: mark anything unverified. Stop after each milestone.
