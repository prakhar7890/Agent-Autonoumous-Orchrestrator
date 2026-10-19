from __future__ import annotations

from agent_repo.manifest import Status
from agent_repo.repository import AgentRepository
from agent_repo.seeds import SEED_MANIFESTS, seed_repository
from tests.conftest import MAINTAINER


import pytest


@pytest.mark.neo4j_smoke
def test_seven_seeds_all_stable(seeded: AgentRepository) -> None:
    names = seeded.list_agents()
    assert names == sorted(m.name for m in SEED_MANIFESTS) and len(names) == 7
    for name in names:
        default = seeded.default_version(name)
        assert default is not None and default.status is Status.STABLE and default.author == MAINTAINER


def test_seeding_is_idempotent(seeded: AgentRepository) -> None:
    seed_repository(seeded, MAINTAINER)
    assert all(len(seeded.versions(n)) == 1 for n in seeded.list_agents())


def test_only_transcriber_is_chainable(seeded: AgentRepository) -> None:
    assert [n for n in seeded.list_agents() if seeded.describe(n).chainable] == ["audio_transcriber"]


def test_describe_has_every_manifest_field(seeded: AgentRepository) -> None:
    d = seeded.describe("text_summarizer")
    assert d.purpose and d.usefulness and d.input_modalities == ["text"] and d.output_type == "text"
    assert d.pipeline == ["1. tool text_summarize {'max_sentences': 2}"]
    assert d.own_tools == ["text_summarize"] and d.example_queries and d.system_prompt
    assert d.provider == "mock" and d.processing_description and d.output_description and d.input_description


@pytest.mark.neo4j_smoke
def test_composite_reports_tools_via_sub_agents(seeded: AgentRepository) -> None:
    d = seeded.describe("meeting_note_taker")
    assert d.own_tools == []
    assert d.sub_agents == ["audio_transcriber@0.1", "text_summarizer@0.1"]
    assert sorted(d.reachable_tools) == ["audio_transcribe (low, STUB) via audio_transcriber@0.1",
                                         "text_summarize (low) via text_summarizer@0.1"]


def test_transitive_high_risk_detection(seeded: AgentRepository) -> None:
    assert seeded.reaches_high_risk("email_sender", "0.1")
    assert not seeded.reaches_high_risk("meeting_note_taker", "0.1")


def test_graph_edges_for_tools_and_sub_agents(seeded: AgentRepository) -> None:
    nt = seeded.get_version("meeting_note_taker", "0.1")
    assert seeded.graph.rels(start=nt.node_id, rel_type="USES_TOOL") == []
    assert len(seeded.graph.rels(start=nt.node_id, rel_type="DELEGATES_TO")) == 2
    em = seeded.get_version("email_sender", "0.1")
    [uses] = seeded.graph.rels(start=em.node_id, rel_type="USES_TOOL")
    tool = seeded.graph.get_node(uses.end)
    assert tool is not None and tool.props["risk"] == "high" and tool.props["is_stub"] is True


def test_catalog_lists_all_agents_and_stubs(seeded: AgentRepository) -> None:
    md = seeded.catalog_markdown()
    for m in SEED_MANIFESTS:
        assert f"## {m.name} @ 0.1 (stable)" in md
    assert "audio_transcribe (low, STUB) via audio_transcriber@0.1" in md
    assert "email_send (high, STUB)" in md

