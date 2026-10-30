# Project Description

## What it does

`agent_repo` is two things working together:

1. **A versioned repository of multimodal agents** kept in a graph database. Each agent does one
   task type (text summarization, audio transcription, image classification, image generation, file
   analysis, meeting notes, email sending). Every agent has an immutable **manifest** describing
   what it does, why it is useful, its input and output types, its pipeline, its prompt, and
   example queries. Versions supersede each other; nothing is hard-deleted.
2. **An autonomous router and executor** that reads a human query, decides from the *facts of the
   request* which agents to run, **explains why** for every step, runs them with explicit
   breakpoints, and remembers the successful workflow so similar future queries reuse it.

## Why it is useful

- New capabilities are added as **data** (a new agent version), not as new branching code.
- Routing is **explainable**: each step records the matched terms and the stored metadata it used.
- Governance is enforced **in code**: only a named human promotes a version to `stable`; an LLM can
  never create a first version, change modalities, or reach a HIGH-risk tool.
- Failures are explicit **breakpoints** with a clarifying question, not silent wrong answers.

## Inputs

The system accepts two kinds of facts per request:

- A **query string** (the intent words).
- An optional **attachment** (audio, image, or file). The payload modality is inferred from the
  attachment itself — its declared modality, MIME type, or file extension — never from the words of
  the query. When nothing is attached, the payload is `text`.

## How a query is processed

1. **Extract facts.** Modality comes from the attachment. A modality word (e.g. "image") is dropped
   from the intent tokens **only when the payload is of that same modality**; in
   "generate an image of a report" (text payload) the word "image" stays, because it names the
   wanted output.
2. **Score agents.** The router scores each agent's `purpose`, `usefulness` and `example_queries`
   against the intent tokens (a pluggable embedder; offline bag-of-words by default) and keeps the
   best match above a configured threshold. There is no intent-to-output table.
3. **Insert prerequisites.** If the payload modality differs from the goal agent's required input,
   a BFS over `chainable`, `stable` agents inserts a converting prerequisite (e.g. audio →
   `audio_transcriber` → `text_summarizer`). A prerequisite that transitively reaches a HIGH-risk
   tool is never inserted.
4. **Check workflow memory.** For the matched query type, the router reuses the stored active
   workflow **only if its goal agent equals the newly scored goal agent**.
5. **Plan and explain.** The plan lists every step with agent, purpose, reason, tools and
   input → output. It is stored on the `Execution` node and on the `INCLUDES` edges.
6. **Execute** under shared policy (retries, step/time/token limits, alternative workflows). Every
   event is written to a scrubbed, event-sourced trace.
7. **Learn.** On success the composed workflow becomes the query type's active workflow. On a final
   break the system files an `Escalation`, asks a clarifying question, and (when appropriate)
   opens an improvement proposal or stores a non-routable draft agent request.

## Workflow of the improvement loop

Unhappy users leave validated feedback → a workflow accumulates enough distinct negative reporters
(or an automatic case fails, or the goal agent breaks in a loop) → a **proposal** opens → an
**untrusted LLM rewrite** proposes a new `candidate` version (only `system_prompt` and `pipeline`
may change) → the **evaluation gate** runs stored `Case`s on baseline vs candidate → a **named
human** approves → the new workflow becomes active for that query type, the old version stays
stored, and rollback is a re-point.

## Outputs

- A per-step explained plan (text and stored JSON).
- The agent output payload (`text`, `json`, or `image`, depending on the goal agent).
- A stored `Execution` subgraph (`agent-repo explain EXECUTION_ID`) with reason, tools and status.
- On failure: a breakpoint name, a message, and a clarifying question.
- A generated agent catalog and a Cypher export of the whole graph.

## What will be built next

See [`ROADMAP_AND_OPEN_QUESTIONS.md`](ROADMAP_AND_OPEN_QUESTIONS.md). In short: real model
integrations behind the existing STUB tools, a dense-embedding router, and (out of scope for this
prototype) a web UI.
