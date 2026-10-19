from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_repo.manifest import AgentManifest, Modality, PipelineStep


def _base(**over: object) -> dict[str, object]:
    data: dict[str, object] = dict(
        name="demo_agent", purpose="does a thing", usefulness="helps", input_modalities=[Modality.TEXT],
        input_description="text", processing_description="steps",
        pipeline=[PipelineStep(kind="tool", name="text_summarize")], output_type=Modality.TEXT,
        output_description="text",
    )
    data.update(over)
    return data


def test_valid_manifest() -> None:
    assert AgentManifest(**_base()).tool_names() == ["text_summarize"]


@pytest.mark.parametrize("field,value", [("purpose", "  "), ("usefulness", ""), ("input_modalities", [])])
def test_required_fields(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        AgentManifest(**_base(**{field: value}))


def test_needs_pipeline_or_prompt() -> None:
    with pytest.raises(ValidationError, match="pipeline or a system prompt"):
        AgentManifest(**_base(pipeline=[], system_prompt=" "))
    assert AgentManifest(**_base(pipeline=[], system_prompt="Summarize."))


def test_sub_agent_must_pin_version() -> None:
    with pytest.raises(ValidationError, match="pin a version"):
        PipelineStep(kind="agent", name="text_summarizer")


def test_unknown_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentManifest(**_base(status="stable"))


def test_manifest_is_frozen() -> None:
    m = AgentManifest(**_base())
    with pytest.raises(ValidationError):
        m.purpose = "changed"  # type: ignore[misc]


def test_hash_is_canonical_and_content_sensitive() -> None:
    a, b = AgentManifest(**_base()), AgentManifest(**_base())
    assert a.content_hash() == b.content_hash()
    assert AgentManifest(**_base(model="other")).content_hash() != a.content_hash()
    assert AgentManifest.from_json(a.canonical_json()) == a

