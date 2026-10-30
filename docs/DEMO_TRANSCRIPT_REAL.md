# End-to-end `ask` transcript with real Groq transcription (`AGENT_REPO_TRANSCRIBER=groq`)

This file records the **real, unedited stdout** of an end-to-end CLI `ask` execution on a
synthesized speech WAV file with `AGENT_REPO_TRANSCRIBER=groq` enabled (`whisper-large-v3`). Unlike
the default offline demo (`docs/DEMO_TRANSCRIPT.md`), the prerequisite `audio_transcribe` step runs
live speech-to-text over the network via Groq (`is_stub=False`) before passing the transcript into
the deterministic `text_summarizer@0.1` goal agent.

No API keys or `.env` values were read or printed in producing this transcript.

## How this transcript was produced

```powershell
# 1. Synthesize a 3-sentence speech WAV locally using Windows SAPI (System.Speech):
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SetOutputToWaveFile("C:\Temp\meeting_clip.wav")
$s.Speak("Alpha release passed all tests on Tuesday. Beta deployment is scheduled for Friday morning. The team approved the rollout plan.")
$s.Dispose()

# 2. Opt in to the real Groq ASR tool (reads GROQ_API_KEY in-process from .env) on a fresh --graph-file:
$env:AGENT_REPO_TRANSCRIBER = "groq"
.venv\Scripts\python.exe -m agent_repo --graph-file C:\Temp\real_transcribe_graph.json ask "summarize this audio recording" --attachment C:\Temp\meeting_clip.wav
.venv\Scripts\python.exe -m agent_repo --graph-file C:\Temp\real_transcribe_graph.json explain 90a945b894094a39b27249352a1636d8
```

- Input file: `meeting_clip.wav` (`410,084` bytes, synthesized spoken text: *"Alpha release passed all tests on Tuesday. Beta deployment is scheduled for Friday morning. The team approved the rollout plan."*).
- Exit code of `ask`: **0**.
- Note that in the plan output below, `audio_transcribe (low) via audio_transcriber@0.1` has **no
  `STUB` marker** (compare `audio_transcribe (low, STUB)` in `docs/DEMO_TRANSCRIPT.md`), and the
  final 2-sentence extractive summary has no `[STUB TRANSCRIPT]` prefix.

## Verbatim `ask` stdout

```text
Status: succeeded
Execution: 90a945b894094a39b27249352a1636d8
Query type: audio->text_summarizer (payload=audio, goal=text_summarizer@0.1)
Dropped payload-redundant modality words: audio, recording
Plan steps:
  1. [prerequisite] audio_transcriber@0.1 (audio -> text)
     What it does: Transcribe speech in an audio recording into text.
     Why chosen:   Inserted automatically as a chainable prerequisite because the attached payload is 'audio' and goal agent 'text_summarizer' requires text.
     How it helps: Turns recordings into text so they can be searched, read, or summarized. Converts 'audio' into 'text'.
     Tools:        audio_transcribe (low) via audio_transcriber@0.1
  2. [goal] text_summarizer@0.1 (text -> text)
     What it does: Summarize long text into a short summary of the key points.
     Why chosen:   Matched request intent terms ['summarize'] against stored metadata (score=0.70).
     How it helps: Saves reading time by condensing documents, notes and transcripts into the main points.
     Tools:        text_summarize (low) via text_summarizer@0.1
Output: Alpha release passed all tests on Tuesday. Beta deployment is scheduled for Friday morning.
```

## Verbatim `explain` stdout (execution subgraph)

```text
Execution: 90a945b894094a39b27249352a1636d8
  Status:     succeeded
  Query:      summarize this audio recording
  QueryType:  audio->text_summarizer
  Workflow:   0fa7ea3cdfef441b83cd0b4b1f96ffb0
  Agents & Tools:
    [1] audio_transcriber@0.1 (role=prerequisite)
        Purpose:         Transcribe speech in an audio recording into text.
        Stored reason:   Inserted automatically as a chainable prerequisite because the attached payload is 'audio' and goal agent 'text_summarizer' requires text.
        Reachable tools: audio_transcribe (low) via audio_transcriber@0.1
    [2] text_summarizer@0.1 (role=goal)
        Purpose:         Summarize long text into a short summary of the key points.
        Stored reason:   Matched request intent terms ['summarize'] against stored metadata (score=0.70).
        Reachable tools: text_summarize (low) via text_summarizer@0.1
  Output:     Alpha release passed all tests on Tuesday. Beta deployment is scheduled for Friday morning.
```

