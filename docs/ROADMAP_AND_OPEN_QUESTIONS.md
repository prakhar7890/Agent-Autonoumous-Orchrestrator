# Roadmap and Open Questions

This document records what is deliberately **not** built yet, and the questions a follow-up would
need to answer. It is not a promise of scope; it is a record so nothing is silently expanded.

## Next steps

1. **Real tool integrations (partly done).** `audio_transcribe` (Groq `whisper-large-v3`) and
   `image_classify` (Gemini vision) now have **opt-in** real implementations selected per tool via
   `AGENT_REPO_TRANSCRIBER` / `AGENT_REPO_IMAGE_CLASSIFIER` (default `stub`); both were verified
   live and are recorded in [`TOOL_INTEGRATIONS.md`](TOOL_INTEGRATIONS.md). **Still open:**
   `image_generate` and `email_send` remain labelled stubs with no real implementation (real image
   generation needs a paid image model; `email_send` needs SMTP credentials and sends real mail).
2. **Real LLM verification (partly done).** **`gemini` (`gemini-3.5-flash`) and `groq`
   (`openai/gpt-oss-20b`) were verified live** and are recorded in
   [`LLM_PROVIDERS.md`](LLM_PROVIDERS.md). **Still open:** the `openai` path is *not verified* (the
   account has no credits), and only the single `generate()` smoke call was exercised live — the full
   router/runner/feedback flow under a live model is not verified (tests and demo still use `MockLLM`).
3. **Denser routing.** Implement a dense-embedding `Embedder` behind the existing protocol (the
   offline `BagOfWordsEmbedder` is the default) and re-tune thresholds; the interface already allows
   it without touching routing logic.
4. **Durable LangGraph checkpointer.** Replace `MemorySaver` with a persistent checkpointer and set
   an explicit msgpack allow-list to remove the `Deserializing unregistered type …` warnings.
5. **Optional local Neo4j.** Verify or remove `deploy/optional/docker-compose.yml`; it is currently
   unrun and must not be described as a verified backend.
6. **Schedule/automation for cases** (cron-style evaluation) and richer analytics on stored
   executions.
7. **Ambiguous-match breakpoint (ask the user when near-top candidates differ in output type).**
   When two or more payload-compatible candidates score within `RouterConfig.tie_break_margin` and
   differ in output type or match only on weak single-word lexical overlap (such as the silent
   misroutes in Set 1 row 13 and Set 2 rows 11 and 14 — see [`ROUTING_EVAL.md`](ROUTING_EVAL.md)),
   escalate with a clarifying question (`ambiguous_match` breakpoint) instead of silently executing
   the slightly higher scorer.
8. **Fresh hand-written Set 3 for an unseen accuracy number.** Because Set 1 (`14/20`) and Set 2
   (`16/20`) are now development sets that have been tuned against and inspected, reporting an
   unseen routing accuracy number requires authoring a fresh hand-written Set 3.
8. **Fresh hand-written Set 3.** The two 20-request paraphrase sets in [`ROUTING_EVAL.md`](ROUTING_EVAL.md)
   are now development sets (both were used to tune or were inspected once), so neither reports an
   unseen accuracy number. An unseen figure needs a new hand-written **Set 3** of paraphrases that is
   written and evaluated **once**, after the router is frozen, and is never used for tuning.

> Done in the "content blocks" phase: normalising structured `AIMessage.content` (Gemini 3.x) to
> plain text via `extract_text()` in `agent_repo/llm.py`, re-verified live.
>
> Done in the "real tools" phase: opt-in real `audio_transcribe` (Groq ASR) and `image_classify`
> (Gemini vision) at the `tools.py` swap point, selected per tool via `ToolSettings`; stubs stay the
> default. Verified live (`docs/TOOL_INTEGRATIONS.md`); `image_generate`/`email_send` remain stubs.

## Open questions

- **Embedding choice and thresholds.** What scoring weights/thresholds generalise beyond the seeded
  agents without reintroducing per-query tuning? `RouterConfig` makes this configurable, but the
  cross-domain sweet spot is unmeasured.
- **Token accounting.** Token use is a deterministic estimate (`estimate_tokens`, ~4 chars/token)
  for offline reproducibility. How should real provider usage be recorded without breaking the
  shared-policy parity between engines?
- **Scrubber precision.** Regex scrubbing misses names and free-form PII and can over-redact
  phone-like numbers. What is the right complement (a PII model? structured provenance?) without
  weakening the offline determinism tests?
- **Human identity.** "Named human" is a convention, not authentication. If this ever needs real
  governance, what identity provider and audit trail would replace `is_human_name`?
- **Multi-goal requests.** The router picks one goal agent per request. Should composite requests be
  split into multiple plans, and how would workflow memory key them?
- **Alternative-workflow budget.** One alternative is the default; what is the right limit before
  cost outweighs robustness?
- **Case authorship.** Who is responsible for maintaining the automatic cases each query type needs
  before a rewrite can be evaluated (`needs_cases`)?

## Explicitly out of scope

- No web UI: the CLI is the prototype's interface.
- No new persistence backend beyond `InMemoryGraph` and Neo4j/AuraDB.
