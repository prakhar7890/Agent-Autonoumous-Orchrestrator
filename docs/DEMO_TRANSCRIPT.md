# Live demo transcript

Real, unedited stdout of a single end-to-end demo run. Ids and timestamps are generated per run,
so a fresh run produces a different transcript byte-for-byte.

## How this transcript was produced

```powershell
# fresh offline salt (required; the app refuses the .env placeholder)
$env:AGENT_REPO_HASH_SALT = python -c "import secrets;print(secrets.token_hex(16))"
$env:PYTHONIOENCODING = "utf-8"

# --graph-file forces the InMemoryGraph backend, so nothing touches AuraDB
python -m agent_repo.cli demo --graph-file C:\Temp\demo_graph.json > demo_stdout.txt
python -m agent_repo.cli cypher --output docs\graph_export_after_demo.cypher --graph-file C:\Temp\demo_graph.json
```

- Exit code of the demo: **0**.
- Backend: **in-memory** (`--graph-file`), seeded automatically with the seven seed agents and the
  `CLI Seed Maintainer` author. No Neo4j/AuraDB connection is used.
- The graph produced by this run was exported to `docs/graph_export_after_demo.cypher`
  (101,963 bytes).
- LangGraph prints deprecation warnings on **stderr** about checkpoint-msgpack deserialising
  unregistered types (`CallContext`, `TraceEvent`, `Modality`, `Plan`, `PlanStep`). Those are on
  stderr only and are omitted below; the transcript is stdout only.

## Transcript (verbatim stdout)

```text
=== M5 acceptance demo (offline stubs are labelled) ===

[1. summarize text]
Status: succeeded
Execution: f725569127e245348f28d387c4d7f993
Query type: text->text_summarizer (payload=text, goal=text_summarizer@0.1)
Plan steps:
  1. [goal] text_summarizer@0.1 (text -> text)
     What it does: Summarize long text into a short summary of the key points.
     Why chosen:   Matched request intent terms ['key', 'point', 'summarize'] against stored metadata (score=0.36).
     How it helps: Saves reading time by condensing documents, notes and transcripts into the main points.
     Tools:        text_summarize (low) via text_summarizer@0.1
Output: summarize the key points of this report: Alpha facts. Beta launch.

[2. summarize audio file (transcriber is inserted)]
Status: succeeded
Execution: e88e40517c684af5a5dc18fe6807c175
Query type: audio->meeting_note_taker (payload=audio, goal=meeting_note_taker@0.1)
Plan steps:
  1. [goal] meeting_note_taker@0.1 (audio -> text)
     What it does: Produce meeting notes from a meeting recording.
     Why chosen:   Matched request intent terms ['meet'] against stored metadata (score=0.24).
     How it helps: Turns a recorded meeting into short notes in one step.
     Tools:        text_summarize (low) via text_summarizer@0.1, audio_transcribe (low, STUB) via audio_transcriber@0.1
Output: [STUB TRANSCRIPT] No speech recognition was performed on demo-audio.wav (21 bytes).

[3. classify an image]
Status: succeeded
Execution: c7cbade72d3b42cfb03faeacee89a398
Query type: image->image_classifier (payload=image, goal=image_classifier@0.1)
Dropped payload-redundant modality words: image
Plan steps:
  1. [goal] image_classifier@0.1 (image -> json)
     What it does: Classify an image and label what it shows.
     Why chosen:   Matched request intent terms ['classify'] against stored metadata (score=0.39).
     How it helps: Quickly tags and categorizes pictures without manual inspection.
     Tools:        image_classify (low, STUB) via image_classifier@0.1
Output: [STUB CLASSIFICATION] food (0.78), person (0.4)

[4. analyze a file]
Status: succeeded
Execution: 451be488ebe44a1aac25f8d4d4e9797a
Query type: file->file_analyzer (payload=file, goal=file_analyzer@0.1)
Dropped payload-redundant modality words: file
Plan steps:
  1. [goal] file_analyzer@0.1 (file -> json)
     What it does: Analyze a file and report its structure and statistics.
     Why chosen:   Matched request intent terms ['analyze', 'report'] against stored metadata (score=0.69).
     How it helps: Gives a fast overview of a document or dataset: size, lines, words, columns, frequent terms.
     Tools:        file_analyze (low) via file_analyzer@0.1
Output: file_name=demo-report.txt, extension=.txt, size_bytes=43, kind=text, lines=1, words=4, top_words=['quarterly', 'report', 'requests', 'completed']

[5. generate an image]
Status: succeeded
Execution: 1134314e398f464dbc8ecf10bbaf7737
Query type: text->image_generator (payload=text, goal=image_generator@0.1)
Plan steps:
  1. [goal] image_generator@0.1 (text -> image)
     What it does: Generate a new image from a text description.
     Why chosen:   Matched request intent terms ['generate', 'image'] against stored metadata (score=0.42).
     How it helps: Creates illustrations or visuals from a written prompt.
     Tools:        image_generate (medium, STUB) via image_generator@0.1
Output: [STUB IMAGE] placeholder SVG for: generate an image of a blue landscape

[6a. HIGH-risk email denied without a grant]
Status: broken
Execution: 10c1f118b34b426987ed173e437dea6a
Query type: text->email_sender (payload=text, goal=email_sender@0.1)
Plan steps:
  1. [goal] email_sender@0.1 (text -> json)
     What it does: Send an email with the given text.
     Why chosen:   Matched request intent terms ['email', 'send', 'team'] against stored metadata (score=0.40).
     How it helps: Delivers results to people by email (HIGH risk: needs an explicit authority grant).
     Tools:        email_send (high, STUB) via email_sender@0.1
Breakpoint: permission_denied
Execution stopped at breakpoint 'permission_denied': tool 'email_send' is HIGH risk and no valid authority grant was given
Clarifying question: This action uses a HIGH-risk tool. Can a named human provide an explicit AuthorityGrant with a reason?
Proposal: f9b84cb88b204dc5aaf6c1b8fe1adc56

[6b. HIGH-risk email allowed with an explicit grant]
Status: succeeded
Execution: 03107f606253443992d9b4e465256375
Query type: text->email_sender (payload=text, goal=email_sender@0.1)
Plan steps:
  1. [goal] email_sender@0.1 (text -> json)
     What it does: Send an email with the given text.
     Why chosen:   Matched request intent terms ['email', 'send', 'team'] against stored metadata (score=0.40).
     How it helps: Delivers results to people by email (HIGH risk: needs an explicit authority grant).
     Tools:        email_send (high, STUB) via email_sender@0.1
Output: [STUB EMAIL] not sent (stub)

[7. no-match breakpoint and draft request]
Status: broken
Execution: d0d2b0a69b134367b0f2569859fab5af
Breakpoint: no_match
Execution stopped at breakpoint 'no_match': No repository agent matched query intent tokens ['triangulate', 'quasar', 'telemetry', 'heliotropic', 'lattice'].
Clarifying question: Could you rephrase the task or tell me which output format (summary, classification, file stats, image) you want?
Draft agent request: c01a297093e140f2aaaf3c1601022685

[8a. missing attachment breakpoint]
Status: broken
Execution: 89c7345173984b9b800b7f03ac332601
Breakpoint: missing_input
Execution stopped at breakpoint 'missing_input': Goal agent 'image_classifier' requires input modality 'image', got 'text', and no safe chainable prerequisite converts between them.
Clarifying question: Could you attach the required file (audio, image, or document) and verify the file path exists?

[8b. missing file breakpoint]
Status: broken
Execution: f9d8f8cc5db04b6cb6fb32b37467b5cd
Breakpoint: missing_input
Execution stopped at breakpoint 'missing_input': Attached file not found: does-not-exist.txt
Clarifying question: Could you attach the required file (audio, image, or document) and verify the file path exists?

[9. same query type reused from workflow memory]
Status: succeeded
Execution: ac67c5f249084b2fb95520f03c2ddb99
Query type: text->text_summarizer (payload=text, goal=text_summarizer@0.1)
Workflow memory: reused active workflow 59c4f3d3e5e844d4961265e69fa21b2f
Plan steps:
  1. [goal] text_summarizer@0.1 (text -> text)
     What it does: Summarize long text into a short summary of the key points.
     Why chosen:   Matched request intent terms ['key', 'point', 'summarize'] against stored metadata (score=0.36).
     How it helps: Saves reading time by condensing documents, notes and transcripts into the main points.
     Tools:        text_summarize (low) via text_summarizer@0.1
Reused workflow: 59c4f3d3e5e844d4961265e69fa21b2f
Output: summarize the key points of this report: Alpha facts. Beta launch.

[10. three distinct users, scripted rewrite and evaluation]
MOCK LLM: scripted rewrite
Candidate: text_summarizer@0.2; evaluation: pending_approval; accuracy 0.0 -> 1.0

[11. human approval and next query uses new version]
Status: succeeded
Execution: aca00a28b03d4c9bbfc1040cba1da343
Query type: text->text_summarizer (payload=text, goal=text_summarizer@0.2)
Workflow memory: reused active workflow 04ea3713977743f1b9f36ef9739ab572
Plan steps:
  1. [goal] text_summarizer@0.2 (text -> text)
     What it does: Summarize long text into a short summary of the key points.
     Why chosen:   Matched request intent terms ['key', 'point', 'summarize'] against stored metadata (score=0.36).
     How it helps: Saves reading time by condensing documents, notes and transcripts into the main points.
     Tools:        text_summarize (low) via text_summarizer@0.2
Reused workflow: 04ea3713977743f1b9f36ef9739ab572
Output: summarize the key points of this report: Alpha facts.
Old version retained: 0.1; lineage: text_summarizer@0.2 <- text_summarizer@0.1
Useless rewrite evaluation: rejected_by_gate (expected rejected_by_gate)

[12. LangGraph plan-approval pause]
Paused before execution; pending plan:
Query type: text->text_summarizer (payload=text, goal=text_summarizer@0.2)
Workflow memory: reused active workflow 04ea3713977743f1b9f36ef9739ab572
Plan steps:
  1. [goal] text_summarizer@0.2 (text -> text)
     What it does: Summarize long text into a short summary of the key points.
     Why chosen:   Matched request intent terms ['key', 'point', 'summarize'] against stored metadata (score=0.36).
     How it helps: Saves reading time by condensing documents, notes and transcripts into the main points.
     Tools:        text_summarize (low) via text_summarizer@0.2
Status: succeeded
Execution: 0a96ae77badb42cc88d9da351686a487
Query type: text->text_summarizer (payload=text, goal=text_summarizer@0.2)
Workflow memory: reused active workflow 04ea3713977743f1b9f36ef9739ab572
Plan steps:
  1. [goal] text_summarizer@0.2 (text -> text)
     What it does: Summarize long text into a short summary of the key points.
     Why chosen:   Matched request intent terms ['key', 'point', 'summarize'] against stored metadata (score=0.36).
     How it helps: Saves reading time by condensing documents, notes and transcripts into the main points.
     Tools:        text_summarize (low) via text_summarizer@0.2
Reused workflow: 04ea3713977743f1b9f36ef9739ab572
Output: summarize this text: LangGraph approval pause.
```
