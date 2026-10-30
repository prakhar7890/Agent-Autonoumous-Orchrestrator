# Real tool integrations

This document records the **real, executed** verification of the opt-in real tool providers behind
the `tools.py` swap point. It is evidence, not a promise. Results below come from actual network
calls made with the keys already present in `.env` (values never read, printed, or stored here).

Environment: Python 3.12 venv, run on 2026-10-19. The provider SDKs (`groq`, `google-genai`) come
with the pinned `providers` extra (`pip install -e ".[providers]"`).

## What is real and how it is selected

Two of the four stub tools now have real implementations. They are **opt-in per tool** through
`ToolSettings` (`agent_repo/config.py`); **the stubs remain the default**, so the offline test suite
and the recorded demo stay deterministic.

| Tool | Default | Real provider | Env flag | Key |
|---|---|---|---|---|
| `audio_transcribe` | stub | Groq ASR (`whisper-large-v3`) | `AGENT_REPO_TRANSCRIBER=groq` | `GROQ_API_KEY` |
| `image_classify` | stub | Gemini vision (`gemini-3.5-flash`) | `AGENT_REPO_IMAGE_CLASSIFIER=gemini` | `GOOGLE_API_KEY` |
| `image_generate` | stub | — (no implementation) | — | — |
| `email_send` | stub | — (no implementation) | — | — |

- A real tool's output is never labelled `stub`; it reports `{"provider": …, "model": …, "stub": False}`.
- Missing key → `ToolError` naming only the env var; provider failure → `ToolError` (retryable). Keys
  are never logged or echoed.
- The model id is an override (`params["model"]`), not a hardcoded constant, for the same reason as
  LLM model ids (provider availability drifts).
- Provider client builders (`_groq_transcription_client`, `_gemini_client`) are module-level, so the
  offline tests (`tests/test_real_tools.py`) substitute a fake client and never touch the network.

## Live verification results

Inputs were generated locally so the test is reproducible and contains no private data:

- **Audio:** a real speech WAV synthesized with the Windows SAPI voice (`System.Speech`), saying
  *"The quick brown fox jumps over the lazy dog. Ship the release on Friday."* (250 692 bytes).
- **Image:** a 64×64 PNG drawn by a small zlib encoder — a red square on a white background.

Both tools were called through the real registry built with
`builtin_registry(tool_settings=ToolSettings(transcriber="groq", image_classifier="gemini"))`:

| Tool | `is_stub` | Model used | Outcome | Result (scrubbed) |
|---|---|---|---|---|
| `audio_transcribe` | False | `whisper-large-v3` | **verified live** | `The quick brown fox jumps over the lazy dog. Ship the release on Friday.` (exact) |
| `image_classify` | False | `gemini-3.5-flash` | **verified live** | `square (0.98) shape (0.85) box (0.65)` |

The ASR transcript matched the spoken sentence exactly. Gemini classified the red square correctly
(top label `square`, 0.98).

## Reproduce

```powershell
pip install -e ".[providers]"      # groq + google-genai SDKs; keys are read from .env, never printed
$env:AGENT_REPO_TRANSCRIBER="groq"
$env:AGENT_REPO_IMAGE_CLASSIFIER="gemini"
& ".\.venv\Scripts\python.exe" -c "from agent_repo.config import ToolSettings; from agent_repo.tools import builtin_registry, Payload, CallContext; from agent_repo.manifest import Modality; r=builtin_registry(tool_settings=ToolSettings(transcriber='groq', image_classifier='gemini')); print(r.call('audio_transcribe', Payload(modality=Modality.AUDIO, path='clip.wav'), {}, CallContext(actor='verifier')).text)"
```

(Any supported audio file works; the CLI uses `AGENT_REPO_TRANSCRIBER`/`AGENT_REPO_IMAGE_CLASSIFIER`
from `.env` automatically via `Settings`.)

## Honesty summary

- **Verified live:** real Groq ASR (`whisper-large-v3`) and real Gemini vision
  (`gemini-3.5-flash`) through the registry, with local inputs.
- **Not verified:** `image_generate` and `email_send` (still stubs, no real implementation); the full
  router/runner/feedback flow driven by a real tool inside a live execution (the call was made
  directly through the registry); any other model id, and any other image/audio format beyond the
  PNG/WAV used here.
- The automated test suite exercises the real code paths **offline only** (fake clients); it never
  calls Groq or Gemini.
