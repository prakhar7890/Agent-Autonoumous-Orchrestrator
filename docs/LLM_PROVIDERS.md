# Live LLM provider verification

This document records the **real, executed** verification of the live LLM provider paths behind
`make_llm("provider:model")` and `LangChainLLM` (`agent_repo/llm.py`). It is evidence, not a
promise: results below come from actual network calls made with the keys in `.env` (values never
read, printed, or stored here).

Environment: Python 3.12 venv, run on 2026-10-19. Provider adapters installed from the
`providers` extra: `langchain-google-genai==4.4.2`, `langchain-groq==1.1.3`,
`langchain-openai==1.7.2`.

Smoke request (identical for every provider): system `"You are a terse smoke test. Reply with a
single word."`, prompt `"Reply with exactly one word: READY"`, through
`make_llm(spec).generate(prompt, system)`.

## Results

| Provider | Key in `.env` | Adapter import | Model used | Outcome | Latency | Reply (scrubbed) |
|---|---|---|---|---|---|---|
| gemini | present | ok | `gemini-3.5-flash` | **verified live** | 3.15 s | `READY` |
| groq | present | ok | `openai/gpt-oss-20b` | **verified live** | 0.55 s | `READY` |
| openai | present | ok | `gpt-4o-mini` | **not verified** — HTTP 429 `insufficient_quota` ("no credits remaining") | — | — |

All three provider packages import and `make_llm` authenticates. The only non-verification is
OpenAI, and the cause is **account billing**, not the code path: the request reached OpenAI and was
rejected for lack of credits.

## Model availability notes (observed, not assumed)

The model names used in the earlier docs were stale, so the smoke test listed what each key can
actually access:

- **Gemini**: the historical `gemini-1.5-flash` and `gemini-2.0-flash` return 404 (`NOT_FOUND`),
  and `gemini-2.5-flash` / `gemini-2.5-flash-lite` are "no longer available to new users". A model
  that works today is **`gemini-3.5-flash`**. `gemini-flash-latest` timed out after 90 s in this run
  (transient, not a code failure).
- **Groq**: `llama-3.1-8b-instant` and `llama-3.3-70b-versatile` return `model_not_found` for this
  key. Working chat models from the account include **`openai/gpt-oss-20b`** and
  `openai/gpt-oss-120b`.
- **OpenAI**: model choice is moot until the account has credits.

Because model availability drifts, `docs/DECISIONS.md` records that provider model ids are inputs to
`make_llm("provider:model")`, not constants in code, so a caller passes a currently valid id.

## Finding: Gemini returns structured content blocks

For the verified Gemini call, `LangChainLLM.generate` returned the message content as a
**list of content blocks** (e.g. `[{"type": "text", "text": "READY", "extras": {"signature": "…"}}]`),
because the adapter does `content if isinstance(content, str) else json.dumps(content)`
(`agent_repo/llm.py:139`). Groq returned a plain string. Consequences:

- The raw text `"READY"` is present, but wrapped in JSON with a provider signature blob.
- `LangChainLLM.draft_agent` (`agent_repo/llm.py:158-172`) `json.loads` the response; for a Gemini
  list-of-blocks reply that parse yields a list, not the expected dict, so it falls back to the
  `raw_suggestion` branch. This is a **real limitation of the current adapter for Gemini 3.x**, not a
  crash.

This was observed during verification. It is now **fixed**: `extract_text()` (`agent_repo/llm.py`)
normalises `AIMessage.content` (plain string, list of `{"type": "text", "text": …}` blocks, or nested
`{"text": {"value": …}}`) to plain text and drops non-text blocks and signatures.
`LangChainLLM.generate` uses it, so `draft_agent` is robust too. Outcome re-verified **live** against
`gemini:gemini-3.5-flash`: `generate(...).text` is now the plain string `'READY'` (previously
JSON-wrapped blocks). Offline coverage is in `tests/test_llm_text_extraction.py`.

## Live CLI proposal rewrite runs (`proposals generate --llm PROVIDER:MODEL`)

The CLI command `agent-repo proposals generate <proposal_id> [--llm PROVIDER:MODEL]` drives
`FeedbackLoop.generate_candidate()` (`agent_repo/feedback.py`) with either the default offline
`MockLLM` or a live `LangChainLLM` constructed via `make_llm(args.llm)`.

Both verified live providers were executed against demo data on a **fresh `--graph-file`
in-memory graph** (seeded with the 7 default agents, one executed `text->text_summarizer` workflow,
the demo's 1-sentence automatic `Case` requiring `must_not_contain: "Charlie"` on the 3-sentence
sample `"Alice led the launch. Bob fixed the bug. Charlie wrote the postmortem."`, and 3 negative
user feedback submissions that opened a `Proposal` for `text_summarizer@0.1`). No keys were read or
printed in the output.

### Run 1 — initial prompt (before fence-stripping and allowed-tools/agents prompt context)

| Provider (`--llm`) | CLI exit code | Candidate created or rejected | CLI / stored reason (`Proposal.rewrite_error`) | Proposal status after call | Evaluation gate result |
|---|---|---|---|---|---|
| `gemini:gemini-3.5-flash` | `2` | **Rejected** (`candidate_version = None`) | `Error: LLM rewrite was not valid JSON` (`rewrite_error = "invalid JSON"`): Gemini wrapped its JSON object in ` ```json … ``` ` markdown code fences. | `open` (unchanged) | **Not run** — no candidate version was created. |
| `groq:openai/gpt-oss-20b` | `2` | **Rejected** (`candidate_version = None`) | `Error: LLM rewrite rejected (RepositoryError)`: Groq returned JSON with `{"system_prompt", "pipeline"}`, which `AgentRepository.create_version()` rejected under repository rules because the prompt did not list the allowed tool/agent names. | `open` (unchanged) | **Not run** — no candidate version was created. |

### Run 2 — after single-fence stripping, sanitized `rewrite_error`, and prompt context (current pipeline, allowed tool names, allowed agent names; guardrails unchanged)

| Provider (`--llm`) | `proposals generate` exit code | Candidate created or rejected | Candidate details & stored `rewrite_error` | `proposals evaluate` gate result |
|---|---|---|---|---|
| `gemini:gemini-3.5-flash` | `0` | **Created** (`Candidate version: 0.2`, `rewrite_label = "gemini:gemini-3.5-flash"`) | `rewrite_error = ""`; updated `system_prompt` and changed `pipeline` to `[{"kind": "tool", "name": "text_summarize", "params": {"max_sentences": 3}}]`. Passed all key, type, modality, and HIGH-risk guardrails. | **`rejected_by_gate`** (`accuracy 1.0 -> 0.0`, 1 regression, `latency_ok=True`, `token_budget_ok=True`) — raising `max_sentences` from `2` to `3` included the third sentence (`"Charlie wrote the postmortem."`), failing the `must_not_contain: "Charlie"` check on case `one_sentence_only`. |
| `groq:openai/gpt-oss-20b` | `0` | **Created** (`Candidate version: 0.2`, `rewrite_label = "groq:openai/gpt-oss-20b"`) | `rewrite_error = ""`; updated `system_prompt` (`"Summarize the given text concisely and accurately, preserving all key points while omitting extraneous details. Do not add or alter facts."`) and kept `pipeline` at `[{"kind": "tool", "name": "text_summarize", "params": {"max_sentences": 2}}]`. Passed all key, type, modality, and HIGH-risk guardrails. | **`rejected_by_gate`** (`accuracy 1.0 -> 1.0`, `regressions=[]`, `latency_ok=True`, `token_budget_ok=True`) — the evaluation gate requires strict accuracy improvement (`candidate_accuracy > baseline_accuracy`), and keeping `max_sentences: 2` left the deterministic `text_summarize` output unchanged on the automatic case. |

Both runs demonstrate the two-layer safety model for untrusted LLM rewrites:
1. `generate_candidate` strips at most one outer markdown code fence, enforces exact JSON keys
   (`{"system_prompt", "pipeline"}`), forbids modality/identity changes and HIGH-risk tools
   (directly or transitively), and records a sanitized rule reason in `Proposal.rewrite_error` when
   any check fails.
2. When a valid candidate `0.2` is created, `evaluate_proposal` runs the workflow's automatic cases
   and blocks any candidate that fails to strictly improve accuracy or introduces a regression
   (`rejected_by_gate`), leaving the active workflow unchanged.

## Reproduce

```powershell
pip install -e ".[providers]"     # installs the pinned adapters
# keys are read from .env by make_llm; nothing is printed
python -c "from agent_repo.llm import make_llm; print(make_llm('groq:openai/gpt-oss-20b').generate('Reply with exactly one word: READY','Reply with a single word.').text)"

# Live proposal rewrite + evaluation on a fresh --graph-file graph:
python -m agent_repo proposals generate <proposal_id> --llm gemini:gemini-3.5-flash --graph-file C:\Temp\prop_gemini.json
python -m agent_repo proposals evaluate <proposal_id> --graph-file C:\Temp\prop_gemini.json
python -m agent_repo proposals generate <proposal_id> --llm groq:openai/gpt-oss-20b   --graph-file C:\Temp\prop_groq.json
python -m agent_repo proposals evaluate <proposal_id> --graph-file C:\Temp\prop_groq.json
```

Model listing used during verification (prints only model ids):

```python
from google import genai; [print(m.name) for m in genai.Client(api_key=...).models.list()]
from groq import Groq;    [print(m.id)   for m in Groq(api_key=...).models.list().data]
```

## Honesty summary

- **Verified live:** `gemini:gemini-3.5-flash` and `groq:openai/gpt-oss-20b` — for direct
  `make_llm(...).generate()` smoke calls, for `agent-repo proposals generate <id> --llm ...`
  creating candidate `text_summarizer@0.2` on fresh `--graph-file` demo data, and for
  `agent-repo proposals evaluate <id>` running the evaluation gate against the live-generated
  candidates (both `rejected_by_gate`).
- **Not verified:** `openai` (account has no credits); every other model id.


