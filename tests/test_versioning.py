"""Every versioning rule from spec §5, on every configured backend."""

from __future__ import annotations

from typing import Any

import pytest

from agent_repo.actors import NotHumanError
from agent_repo.manifest import AgentManifest, Modality, PipelineStep, Status
from agent_repo.repository import (
    ALLOWED_TRANSITIONS, AgentRepository, GuardrailError, RepositoryError, TransitionError,
)
from agent_repo.seeds import SEED_MANIFESTS
from tests.conftest import MAINTAINER, tamper


def variant(repo: AgentRepository, agent: str, version: str = "0.1", **update: Any) -> AgentManifest:
    base = repo.get_version(agent, version).manifest.model_dump()
    return AgentManifest(**{**base, **update})


def promote(repo: AgentRepository, agent: str, version: str, who: str = MAINTAINER) -> None:
    repo.submit_for_approval(agent, version, who)
    repo.approve(agent, version, who)


# ---------------------------------------------------------------- immutability, lineage, integrity

@pytest.mark.neo4j_smoke
def test_change_creates_new_version_linked_by_supersedes(seeded: AgentRepository) -> None:
    v2 = seeded.create_version("text_summarizer", variant(seeded, "text_summarizer", system_prompt="Be brief."),
                               "bob", "shorter prompt")
    assert (v2.version, v2.status) == ("0.2", Status.CANDIDATE)
    assert [r.version for r in seeded.lineage("text_summarizer", "0.2")] == ["0.2", "0.1"]
    sup = seeded.graph.rels(start=v2.node_id, rel_type="SUPERSEDES")
    assert len(sup) == 1 and sup[0].end == seeded.get_version("text_summarizer", "0.1").node_id
    assert seeded.get_version("text_summarizer", "0.1").manifest.system_prompt != "Be brief."


def test_identical_manifest_is_not_a_new_version(seeded: AgentRepository) -> None:
    with pytest.raises(RepositoryError, match="identical"):
        seeded.create_version("text_summarizer", seeded.get_version("text_summarizer", "0.1").manifest, "bob", "x")


def test_manifest_cannot_be_updated_in_place(seeded: AgentRepository) -> None:
    node_id = seeded.get_version("text_summarizer", "0.1").node_id
    with pytest.raises(Exception, match="immutable"):
        seeded.graph.set_props(node_id, {"manifest_json": "{}"})
    assert not [m for m in dir(seeded) if m.startswith(("update", "edit", "delete", "remove"))]


@pytest.mark.neo4j_smoke
def test_integrity_check_detects_tampering(seeded: AgentRepository) -> None:
    assert seeded.verify_integrity() == []
    node = seeded.graph.get_node(seeded.get_version("image_classifier", "0.1").node_id)
    assert node is not None
    tamper(seeded.graph, node, "manifest_json", node.props["manifest_json"].replace("top_k", "top_n"))
    issues = seeded.verify_integrity()
    assert [(i.agent, i.version, i.problem) for i in issues] == [("image_classifier", "0.1", "content hash mismatch")]


@pytest.mark.parametrize("update", [
    {"system_prompt": "new prompt"},
    {"model": "gemini-1.5-flash", "provider": "gemini"},
    {"pipeline": [PipelineStep(kind="tool", name="text_summarize", params={"max_sentences": 3})]},
])
def test_everything_is_versioned(seeded: AgentRepository, update: dict[str, Any]) -> None:
    v2 = seeded.create_version("text_summarizer", variant(seeded, "text_summarizer", **update), "bob", "change")
    assert v2.content_hash != seeded.get_version("text_summarizer", "0.1").content_hash


def test_sub_agent_change_is_versioned(seeded: AgentRepository) -> None:
    v2 = seeded.create_version("text_summarizer", variant(seeded, "text_summarizer", system_prompt="v2"), "bob", "x")
    promote(seeded, "text_summarizer", v2.version)
    m = variant(seeded, "meeting_note_taker", pipeline=[PipelineStep(kind="agent", name="audio_transcriber",
                                                                     version="0.1"),
                                                        PipelineStep(kind="agent", name="text_summarizer",
                                                                     version="0.2")])
    nt2 = seeded.create_version("meeting_note_taker", m, "bob", "use summarizer 0.2")
    targets = {seeded.graph.get_node(r.end).props["version"]  # type: ignore[union-attr]
               for r in seeded.graph.rels(start=nt2.node_id, rel_type="DELEGATES_TO")}
    assert targets == {"0.1", "0.2"}


# ---------------------------------------------------------------- no hard delete

def test_deprecate_keeps_version_stored(seeded: AgentRepository) -> None:
    seeded.deprecate("image_generator", "0.1", "bob", "replaced")
    rec = seeded.get_version("image_generator", "0.1")
    assert rec.status is Status.DEPRECATED
    assert rec.manifest.name == "image_generator"
    assert "image_generator" in seeded.list_agents()


def test_old_versions_remain_after_new_ones(seeded: AgentRepository) -> None:
    v2 = seeded.create_version("text_summarizer", variant(seeded, "text_summarizer", system_prompt="v2"), "bob", "x")
    promote(seeded, "text_summarizer", v2.version)
    assert [r.version for r in seeded.versions("text_summarizer")] == ["0.1", "0.2"]
    assert seeded.get_version("text_summarizer", "0.1").status is Status.STABLE


# ---------------------------------------------------------------- status lifecycle

ALL_PAIRS = [(a, b) for a in Status for b in Status if a != b]


def _reach(repo: AgentRepository, agent: str, version: str, status: Status) -> None:
    path = {Status.CANDIDATE: [], Status.PENDING_APPROVAL: [Status.PENDING_APPROVAL],
            Status.STABLE: [Status.PENDING_APPROVAL, Status.STABLE],
            Status.DEPRECATED: [Status.PENDING_APPROVAL, Status.STABLE, Status.DEPRECATED],
            Status.REJECTED: [Status.REJECTED]}[status]
    for step in path:
        repo.transition(agent, version, step, MAINTAINER, "test")


@pytest.mark.parametrize(("src", "dst"), ALL_PAIRS)
def test_transition_table(seeded: AgentRepository, src: Status, dst: Status) -> None:
    v = seeded.create_version("text_summarizer", variant(seeded, "text_summarizer", system_prompt=f"t_{src}_{dst}"),
                              "bob", "x")
    _reach(seeded, "text_summarizer", v.version, src)
    if dst in ALLOWED_TRANSITIONS[src]:
        assert seeded.transition("text_summarizer", v.version, dst, MAINTAINER, "ok").status is dst
    else:
        with pytest.raises(TransitionError, match="illegal"):
            seeded.transition("text_summarizer", v.version, dst, MAINTAINER, "no")


def test_new_versions_always_start_as_candidate(seeded: AgentRepository) -> None:
    v = seeded.create_version("text_summarizer", variant(seeded, "text_summarizer", system_prompt="t"), "bob", "x")
    assert v.status is Status.CANDIDATE
    with pytest.raises(TransitionError):
        seeded.approve("text_summarizer", v.version, "bob")  # must pass through pending_approval


@pytest.mark.neo4j_smoke
@pytest.mark.parametrize("who", ("llm", "LLM-reviewer", "llm:gemini", "system", "System-bot", " system", "", "   "))
def test_only_named_humans_promote_to_stable(seeded: AgentRepository, who: str) -> None:
    v = seeded.create_version("text_summarizer", variant(seeded, "text_summarizer", system_prompt="t"), "bob", "x")
    seeded.submit_for_approval("text_summarizer", v.version, "bob")
    with pytest.raises(NotHumanError):
        seeded.approve("text_summarizer", v.version, who)
    assert seeded.get_version("text_summarizer", v.version).status is Status.PENDING_APPROVAL


@pytest.mark.parametrize("who", ("llm-bot", "system"))
def test_non_humans_cannot_deprecate_or_set_default(seeded: AgentRepository, who: str) -> None:
    with pytest.raises(NotHumanError):
        seeded.deprecate("text_summarizer", "0.1", who, "x")
    with pytest.raises(NotHumanError):
        seeded.set_default_version("text_summarizer", "0.1", who)


def test_approving_later_version_keeps_default(seeded: AgentRepository) -> None:
    v2 = seeded.create_version("text_summarizer", variant(seeded, "text_summarizer", system_prompt="v2"), "bob", "x")
    promote(seeded, "text_summarizer", v2.version, "carol")
    default = seeded.default_version("text_summarizer")
    assert default is not None and default.version == "0.1"
    seeded.set_default_version("text_summarizer", "0.2", "carol")  # explicit human action only
    assert seeded.default_version("text_summarizer").version == "0.2"  # type: ignore[union-attr]


def test_default_must_be_stable(seeded: AgentRepository) -> None:
    v2 = seeded.create_version("text_summarizer", variant(seeded, "text_summarizer", system_prompt="v2"), "bob", "x")
    with pytest.raises(RepositoryError, match="stable"):
        seeded.set_default_version("text_summarizer", v2.version, "carol")


# ---------------------------------------------------------------- guardrails for non-human authors

@pytest.mark.parametrize("who", ["llm", "system:auto"])
def test_non_human_cannot_create_first_version(repo: AgentRepository, who: str) -> None:
    with pytest.raises(NotHumanError):
        repo.create_agent(SEED_MANIFESTS[0], who)
    assert repo.list_agents() == []


def test_duplicate_agent_refused(seeded: AgentRepository) -> None:
    with pytest.raises(RepositoryError, match="already exists"):
        seeded.create_agent(SEED_MANIFESTS[0], MAINTAINER)


@pytest.mark.parametrize("update", [
    {"input_modalities": [Modality.TEXT, Modality.FILE]},
    {"output_type": Modality.JSON},
])
def test_llm_cannot_change_modalities(seeded: AgentRepository, update: dict[str, Any]) -> None:
    with pytest.raises(GuardrailError, match="modalities"):
        seeded.create_version("text_summarizer", variant(seeded, "text_summarizer", system_prompt="z", **update),
                              "llm:mock", "x")
    # a named human may change modalities (creates a candidate that still needs approval)
    assert seeded.create_version("text_summarizer", variant(seeded, "text_summarizer", **update), "bob", "x")


def test_llm_cannot_add_high_risk_tool_directly(seeded: AgentRepository) -> None:
    m = variant(seeded, "text_summarizer", pipeline=[PipelineStep(kind="tool", name="text_summarize"),
                                                     PipelineStep(kind="tool", name="email_send")])
    with pytest.raises(GuardrailError, match="HIGH-risk"):
        seeded.create_version("text_summarizer", m, "llm:mock", "x")


@pytest.mark.neo4j_smoke
def test_llm_cannot_add_high_risk_tool_via_sub_agent(seeded: AgentRepository) -> None:
    m = variant(seeded, "text_summarizer", pipeline=[PipelineStep(kind="tool", name="text_summarize"),
                                                     PipelineStep(kind="agent", name="email_sender", version="0.1")])
    with pytest.raises(GuardrailError, match="HIGH-risk"):
        seeded.create_version("text_summarizer", m, "llm:mock", "x")


def test_unknown_tool_and_sub_agent_refused(seeded: AgentRepository) -> None:
    with pytest.raises(RepositoryError, match="unknown tool"):
        seeded.create_version("text_summarizer", variant(
            seeded, "text_summarizer", pipeline=[PipelineStep(kind="tool", name="rm_rf")]), "bob", "x")
    with pytest.raises(RepositoryError, match="unknown version"):
        seeded.create_version("text_summarizer", variant(
            seeded, "text_summarizer", pipeline=[PipelineStep(kind="agent", name="ghost", version="0.1")]), "bob", "x")


def test_sub_agent_must_be_stable(seeded: AgentRepository) -> None:
    seeded.create_version("text_summarizer", variant(seeded, "text_summarizer", system_prompt="c"), "bob", "x")
    m = variant(seeded, "meeting_note_taker", pipeline=[PipelineStep(kind="agent", name="text_summarizer",
                                                                     version="0.2")])
    with pytest.raises(RepositoryError, match="not stable"):
        seeded.create_version("meeting_note_taker", m, "bob", "x")


def test_manifest_with_secret_is_refused(seeded: AgentRepository) -> None:
    m = variant(seeded, "text_summarizer", system_prompt="Use api_key=abc123 when calling.")
    with pytest.raises(GuardrailError, match="secret"):
        seeded.create_version("text_summarizer", m, "bob", "x")


def test_change_note_is_scrubbed(seeded: AgentRepository) -> None:
    v = seeded.create_version("text_summarizer", variant(seeded, "text_summarizer", system_prompt="n"), "bob",
                              "asked by bob@example.com")
    assert v.change_note == "asked by [REDACTED_EMAIL]"
