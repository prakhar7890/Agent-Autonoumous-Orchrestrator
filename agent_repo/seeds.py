"""The 7 seed agents (spec §8). Seeds are authored and approved by a named human maintainer,
because non-human authors may not create first versions or promote to stable."""

from __future__ import annotations

from agent_repo.manifest import AgentManifest, Modality, PipelineStep
from agent_repo.repository import AgentRepository

T, A, I, F, J = Modality.TEXT, Modality.AUDIO, Modality.IMAGE, Modality.FILE, Modality.JSON


def _tool(name: str, **params: object) -> PipelineStep:
    return PipelineStep(kind="tool", name=name, params=dict(params))


def _agent(name: str, version: str) -> PipelineStep:
    return PipelineStep(kind="agent", name=name, version=version)


SEED_MANIFESTS: tuple[AgentManifest, ...] = (
    AgentManifest(
        name="text_summarizer",
        purpose="Summarize long text into a short summary of the key points.",
        usefulness="Saves reading time by condensing documents, notes and transcripts into the main points.",
        input_modalities=[T], input_description="Plain text (a document, notes, or a transcript).",
        processing_description="Scores sentences by word frequency and keeps the most informative ones in order.",
        pipeline=[_tool("text_summarize", max_sentences=2)],
        system_prompt="You summarize text faithfully and briefly. Never invent facts.",
        output_type=T, output_description="A short extractive summary.",
        example_queries=["summarize this text", "give me a short summary of the key points", "tl;dr this article",
                         "condense these notes"],
    ),
    AgentManifest(
        name="audio_transcriber",
        purpose="Transcribe speech in an audio recording into text.",
        usefulness="Turns recordings into text so they can be searched, read, or summarized.",
        input_modalities=[A], input_description="An attached audio file (e.g. .wav, .mp3).",
        processing_description="Runs speech-to-text on the audio file (STUB in this prototype).",
        pipeline=[_tool("audio_transcribe")],
        output_type=T, output_description="The transcript text.",
        example_queries=["transcribe this recording", "convert speech to text", "what is said in this audio"],
        chainable=True,
    ),
    AgentManifest(
        name="image_classifier",
        purpose="Classify an image and label what it shows.",
        usefulness="Quickly tags and categorizes pictures without manual inspection.",
        input_modalities=[I], input_description="An attached image file (e.g. .png, .jpg).",
        processing_description="Runs an image classifier and returns the top labels with scores (STUB).",
        pipeline=[_tool("image_classify", top_k=2)],
        output_type=J, output_description="JSON list of labels with confidence scores.",
        example_queries=["classify this picture", "what is in this photo", "label this image",
                         "what category does this belong to"],
    ),
    AgentManifest(
        name="image_generator",
        purpose="Generate a new image from a text description.",
        usefulness="Creates illustrations or visuals from a written prompt.",
        input_modalities=[T], input_description="A text prompt describing the wanted picture.",
        processing_description="Sends the prompt to an image generation model (STUB: placeholder SVG).",
        pipeline=[_tool("image_generate")],
        output_type=I, output_description="An image (SVG placeholder in this prototype).",
        example_queries=["generate an image of a sunset", "draw a picture of a cat", "create an illustration",
                         "make a picture for my slide"],
    ),
    AgentManifest(
        name="file_analyzer",
        purpose="Analyze a file and report its structure and statistics.",
        usefulness="Gives a fast overview of a document or dataset: size, lines, words, columns, frequent terms.",
        input_modalities=[F], input_description="An attached file (text, CSV, or other).",
        processing_description="Reads the file and computes statistics; CSVs also get columns and row counts.",
        pipeline=[_tool("file_analyze", top_n=5)],
        output_type=J, output_description="JSON report with file statistics.",
        example_queries=["analyze this file", "what is in this document", "give me statistics for this csv",
                         "inspect this dataset"],
    ),
    AgentManifest(
        name="meeting_note_taker",
        purpose="Produce meeting notes from a meeting recording.",
        usefulness="Turns a recorded meeting into short notes in one step.",
        input_modalities=[A], input_description="An attached audio recording of a meeting.",
        processing_description="Delegates to the audio transcriber, then to the text summarizer.",
        pipeline=[_agent("audio_transcriber", "0.1"), _agent("text_summarizer", "0.1")],
        output_type=T, output_description="Short meeting notes.",
        example_queries=["take notes for this meeting recording", "meeting minutes from this call",
                         "write up the meeting notes"],
    ),
    AgentManifest(
        name="email_sender",
        purpose="Send an email with the given text.",
        usefulness="Delivers results to people by email (HIGH risk: needs an explicit authority grant).",
        input_modalities=[T], input_description="The email body text.",
        processing_description="Calls the mail gateway (STUB: never actually sends).",
        pipeline=[_tool("email_send")],
        output_type=J, output_description="JSON delivery receipt.",
        example_queries=["send an email", "email this to the team", "mail this message"],
    ),
)


def seed_repository(repo: AgentRepository, maintainer: str) -> None:
    """Create and approve every seed agent that does not exist yet (order respects sub-agent pins).
    Uses a single batched transaction on Neo4j/AuraDB when supported.
    """
    from agent_repo.actors import require_human
    from agent_repo.graph.store import new_id
    from agent_repo.manifest import Status

    maintainer = require_human(maintainer, "seeding the repository")
    batch_fn = getattr(repo.graph, "seed_agents_batch", None)
    if callable(batch_fn):
        now = repo._now()  # noqa: SLF001
        seen_seeds: set[tuple[str, str]] = set()
        items = []
        for m in SEED_MANIFESTS:
            for step in m.pipeline:
                if step.kind == "tool" and not repo.tools.has(step.name):
                    from agent_repo.repository import RepositoryError
                    raise RepositoryError(f"unknown tool {step.name!r}")
                if step.kind == "agent" and (step.name, step.version or "0.1") not in seen_seeds:
                    repo.get_version(step.name, step.version or "0.1")
            seen_seeds.add((m.name, "0.1"))
            raw = m.canonical_json()
            items.append({
                "name": m.name,
                "agent_id": new_id(),
                "version_id": new_id(),
                "hv_id": new_id(),
                "agent_props": {
                    "name": m.name, "created_by": maintainer, "created_at": now, "default_version": "0.1",
                },
                "version_props": {
                    "agent": m.name, "version": "0.1", "version_seq": 1,
                    "status": Status.STABLE.value, "author": maintainer, "change_note": "seed",
                    "manifest_json": raw, "content_hash": m.content_hash(), "created_at": now,
                    "purpose": m.purpose, "provider": m.provider, "model": m.model,
                    "pending_approval_by": maintainer, "pending_approval_at": now,
                    "stable_by": maintainer, "stable_at": now, "status_reason": "",
                },
                "tool_steps": [
                    {"name": s.name, "order": i, "rel_id": new_id()}
                    for i, s in enumerate(m.pipeline) if s.kind == "tool"
                ],
                "agent_steps": [
                    {"name": s.name, "version": s.version or "0.1", "order": i, "rel_id": new_id()}
                    for i, s in enumerate(m.pipeline) if s.kind == "agent"
                ],
            })
        batch_fn(items)
        return

    existing = set(repo.list_agents())
    for manifest in SEED_MANIFESTS:
        if manifest.name in existing:
            continue
        rec = repo.create_agent(manifest, author=maintainer, change_note="seed")
        repo.submit_for_approval(rec.agent, rec.version, maintainer)
        repo.approve(rec.agent, rec.version, maintainer)


