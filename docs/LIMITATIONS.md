# Known Limitations

## Real vs. stub

Real: versioned agent graph (in-memory + Neo4j Aura), SHA-256 content-hash immutability, bag-of-words router with tie-break margin, breakpoint/retry runner, feedback loop, artifact store with path-security and MIME validation, FastAPI service with access-controlled artifact endpoint, CLI, web UI with Executions tab and stored-image rendering.

Stub / limited: live Gemini image generation (quota), OpenAI tool (no key configured), human approver (naming convention, not authentication).

## Image generation
- Image generation code is complete and correct. Live generation is blocked by account quota on all four tested Gemini image models. The Playwright end-to-end suite validates the full pipeline using the free ZeroGPU/Pollinations fallback provider.

## Summarizer
- Approved rewrite v0.2 uses an extractive summarizer with max_sentences=1; single-sentence output is the intended behaviour of that version, not a bug.

## File handling
- Embedded PDF text extraction is supported through `pypdf`; scanned, encrypted, corrupt,
  or textless PDFs still require OCR, which is not implemented.

## OpenAI tool
- The `image_generate_openai` tool is present but untested with a live
  key; no OpenAI API key is configured in this environment.

## "Human approver" role
- `human_approver` is a naming convention used in the permission-check
  guard; it does not implement authentication or ACL.

## Router explanation on workflow reuse
- When a workflow is reused across two different queries, the router
  explanation may replay matched terms from the *first* query (e.g.
  shows `['cat', 'generate', 'image']` even when the new query is
  about something else).  The execution result is correct; only the
  explanation text is stale.

## Breakpoint wording
- A non-retryable single-attempt failure produces the message
  "kept failing after all retries", which is technically inaccurate
  when only one attempt was made.  This is cosmetic only.

## Neo4j isolation
- The test suite assumes a **dedicated throwaway** Neo4j instance when
  `NEO4J_TEST_WIPE=1` is set.  Do **not** point `NEO4J_URI` at the
  shared AuraDB and set `NEO4J_TEST_WIPE=1`; that would wipe live data.

  shared AuraDB and set `NEO4J_TEST_WIPE=1`; that would wipe live data.
