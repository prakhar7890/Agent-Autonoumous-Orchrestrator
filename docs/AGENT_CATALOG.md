# Agent Catalog

_Generated from the graph. STUB marks tools that are deterministic placeholders, not real models._

## audio_transcriber @ 0.1 (stable)

- **Purpose:** Transcribe speech in an audio recording into text.
- **Why useful:** Turns recordings into text so they can be searched, read, or summarized.
- **Input:** audio — An attached audio file (e.g. .wav, .mp3).
- **Processing:** Runs speech-to-text on the audio file (STUB in this prototype).
- **Output:** text — The transcript text.
- **Chainable prerequisite:** yes
- **Model:** mock:mock-1
- **Sub-agents:** none
- **Tools (incl. via sub-agents):** audio_transcribe (low, STUB) via audio_transcriber@0.1
- **Pipeline:**
  - 1. tool audio_transcribe
- **Example queries:**
  - transcribe this recording
  - convert speech to text
  - what is said in this audio

## email_sender @ 0.1 (stable)

- **Purpose:** Send an email with the given text.
- **Why useful:** Delivers results to people by email (HIGH risk: needs an explicit authority grant).
- **Input:** text — The email body text.
- **Processing:** Calls the mail gateway (STUB: never actually sends).
- **Output:** json — JSON delivery receipt.
- **Chainable prerequisite:** no
- **Model:** mock:mock-1
- **Sub-agents:** none
- **Tools (incl. via sub-agents):** email_send (high, STUB) via email_sender@0.1
- **Pipeline:**
  - 1. tool email_send
- **Example queries:**
  - send an email
  - email this to the team
  - mail this message

## file_analyzer @ 0.1 (stable)

- **Purpose:** Analyze a file and report its structure and statistics.
- **Why useful:** Gives a fast overview of a document or dataset: size, lines, words, columns, frequent terms.
- **Input:** file — An attached file (text, CSV, or other).
- **Processing:** Reads the file and computes statistics; CSVs also get columns and row counts.
- **Output:** json — JSON report with file statistics.
- **Chainable prerequisite:** no
- **Model:** mock:mock-1
- **Sub-agents:** none
- **Tools (incl. via sub-agents):** file_analyze (low) via file_analyzer@0.1
- **Pipeline:**
  - 1. tool file_analyze {'top_n': 5}
- **Example queries:**
  - analyze this file
  - what is in this document
  - give me statistics for this csv
  - inspect this dataset

## image_classifier @ 0.1 (stable)

- **Purpose:** Classify an image and label what it shows.
- **Why useful:** Quickly tags and categorizes pictures without manual inspection.
- **Input:** image — An attached image file (e.g. .png, .jpg).
- **Processing:** Runs an image classifier and returns the top labels with scores (STUB).
- **Output:** json — JSON list of labels with confidence scores.
- **Chainable prerequisite:** no
- **Model:** mock:mock-1
- **Sub-agents:** none
- **Tools (incl. via sub-agents):** image_classify (low, STUB) via image_classifier@0.1
- **Pipeline:**
  - 1. tool image_classify {'top_k': 2}
- **Example queries:**
  - classify this picture
  - what is in this photo
  - label this image
  - what category does this belong to

## image_generator @ 0.1 (stable)

- **Purpose:** Generate a new image from a text description.
- **Why useful:** Creates illustrations or visuals from a written prompt.
- **Input:** text — A text prompt describing the wanted picture.
- **Processing:** Sends the prompt to an image generation model (STUB: placeholder SVG).
- **Output:** image — An image (SVG placeholder in this prototype).
- **Chainable prerequisite:** no
- **Model:** mock:mock-1
- **Sub-agents:** none
- **Tools (incl. via sub-agents):** image_generate (medium, STUB) via image_generator@0.1
- **Pipeline:**
  - 1. tool image_generate
- **Example queries:**
  - generate an image of a sunset
  - draw a picture of a cat
  - create an illustration
  - make a picture for my slide

## meeting_note_taker @ 0.1 (stable)

- **Purpose:** Produce meeting notes from a meeting recording.
- **Why useful:** Turns a recorded meeting into short notes in one step.
- **Input:** audio — An attached audio recording of a meeting.
- **Processing:** Delegates to the audio transcriber, then to the text summarizer.
- **Output:** text — Short meeting notes.
- **Chainable prerequisite:** no
- **Model:** mock:mock-1
- **Sub-agents:** audio_transcriber@0.1, text_summarizer@0.1
- **Tools (incl. via sub-agents):** text_summarize (low) via text_summarizer@0.1, audio_transcribe (low, STUB) via audio_transcriber@0.1
- **Pipeline:**
  - 1. agent audio_transcriber@0.1
  - 2. agent text_summarizer@0.1
- **Example queries:**
  - take notes for this meeting recording
  - meeting minutes from this call
  - write up the meeting notes

## text_summarizer @ 0.1 (stable)

- **Purpose:** Summarize long text into a short summary of the key points.
- **Why useful:** Saves reading time by condensing documents, notes and transcripts into the main points.
- **Input:** text — Plain text (a document, notes, or a transcript).
- **Processing:** Scores sentences by word frequency and keeps the most informative ones in order.
- **Output:** text — A short extractive summary.
- **Chainable prerequisite:** no
- **Model:** mock:mock-1
- **Sub-agents:** none
- **Tools (incl. via sub-agents):** text_summarize (low) via text_summarizer@0.1
- **Pipeline:**
  - 1. tool text_summarize {'max_sentences': 2}
- **Example queries:**
  - summarize this text
  - give me a short summary of the key points
  - tl;dr this article
  - condense these notes

