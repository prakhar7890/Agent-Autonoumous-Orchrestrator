from __future__ import annotations

from pathlib import Path

import pytest

from agent_repo.manifest import Modality
from agent_repo.tools import (
    AuthorityGrant, CallContext, MissingInputError, Payload, PermissionDeniedError, ToolError, ToolRegistry,
    ToolSpec, UnknownToolError, builtin_registry,
)
from agent_repo.manifest import RiskTier

TEXT = Payload(modality=Modality.TEXT, text="Hello team. Results are good.")


def test_high_risk_denied_without_grant_and_audited(registry: ToolRegistry) -> None:
    with pytest.raises(PermissionDeniedError):
        registry.call("email_send", TEXT, {}, CallContext(actor="bob"))
    last = registry.audit_log[-1]
    assert (last.tool, last.outcome, last.risk) == ("email_send", "denied", "high")


def test_high_risk_allowed_with_human_grant(registry: ToolRegistry) -> None:
    ctx = CallContext(actor="bob", grants=(AuthorityGrant("email_send", "carol", "approved send to team"),))
    out = registry.call("email_send", TEXT, {}, ctx)
    assert out.data == {"sent": False, "stub": True, "chars": len(TEXT.text or "")}
    assert registry.audit_log[-1].outcome == "ok"


@pytest.mark.parametrize("grant", [
    AuthorityGrant("email_send", "llm:gemini", "I approve myself"),
    AuthorityGrant("email_send", "system", "auto"),
    AuthorityGrant("email_send", "carol", "  "),
    AuthorityGrant("image_generate", "carol", "wrong tool"),
])
def test_invalid_grants_are_refused(registry: ToolRegistry, grant: AuthorityGrant) -> None:
    with pytest.raises(PermissionDeniedError):
        registry.call("email_send", TEXT, {}, CallContext(actor="bob", grants=(grant,)))


def test_low_risk_needs_no_grant(registry: ToolRegistry) -> None:
    assert registry.call("text_summarize", TEXT, {"max_sentences": 1}, CallContext(actor="bob")).text


def test_audit_is_scrubbed() -> None:
    reg = builtin_registry()
    with pytest.raises(PermissionDeniedError):
        reg.call("email_send", TEXT, {}, CallContext(actor="bob@example.com"))
    assert reg.audit_log[-1].actor == "[REDACTED_EMAIL]"


def test_wrong_modality_is_missing_input(registry: ToolRegistry) -> None:
    with pytest.raises(MissingInputError, match="needs audio"):
        registry.call("audio_transcribe", TEXT, {}, CallContext(actor="bob"))


@pytest.mark.parametrize("tool,modality", [("audio_transcribe", Modality.AUDIO), ("image_classify", Modality.IMAGE),
                                           ("file_analyze", Modality.FILE)])
def test_missing_or_absent_file_is_missing_input(registry: ToolRegistry, tool: str, modality: Modality,
                                                 tmp_path: Path) -> None:
    ctx = CallContext(actor="bob")
    with pytest.raises(MissingInputError, match="none was given"):
        registry.call(tool, Payload(modality=modality), {}, ctx)
    with pytest.raises(MissingInputError, match="not found"):
        registry.call(tool, Payload(modality=modality, path=str(tmp_path / "nope.bin")), {}, ctx)


def test_unknown_tool(registry: ToolRegistry) -> None:
    with pytest.raises(UnknownToolError):
        registry.call("rm_rf", TEXT, {}, CallContext(actor="bob"))


def test_crashing_tool_becomes_tool_error() -> None:
    def boom(p: Payload, params: dict[str, object]) -> Payload:
        raise RuntimeError("kaboom")

    reg = ToolRegistry()
    reg.register(ToolSpec("boom", "test", RiskTier.LOW, (Modality.TEXT,), Modality.TEXT, False, boom))
    with pytest.raises(ToolError, match="kaboom"):
        reg.call("boom", TEXT, {}, CallContext(actor="bob"))
    assert reg.audit_log[-1].outcome == "error"


def test_stubs_are_labelled(registry: ToolRegistry, tmp_path: Path) -> None:
    stubs = {s.name for s in registry.specs() if s.is_stub}
    assert stubs == {"audio_transcribe", "image_classify", "image_generate", "email_send"}
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG fake")
    ctx = CallContext(actor="bob")
    out = registry.call("image_classify", Payload(modality=Modality.IMAGE, path=str(img)), {}, ctx)
    assert out.data and out.data["stub"] is True and (out.text or "").startswith("[STUB")
    gen = registry.call("image_generate", Payload(modality=Modality.TEXT, text="a cat"), {}, ctx)
    assert gen.modality is Modality.IMAGE and gen.data and gen.data["stub"] is True


def test_stub_outputs_are_deterministic(registry: ToolRegistry, tmp_path: Path) -> None:
    audio = tmp_path / "m.wav"
    audio.write_bytes(b"RIFF....")
    (tmp_path / "m.transcript.txt").write_text("We agreed to ship on Friday.", encoding="utf-8")
    ctx = CallContext(actor="bob")
    p = Payload(modality=Modality.AUDIO, path=str(audio))
    assert registry.call("audio_transcribe", p, {}, ctx) == registry.call("audio_transcribe", p, {}, ctx)
    assert "ship on Friday" in (registry.call("audio_transcribe", p, {}, ctx).text or "")


def test_file_analyze_csv(registry: ToolRegistry, tmp_path: Path) -> None:
    f = tmp_path / "d.csv"
    f.write_text("name,score\nann,3\nbo,4\n", encoding="utf-8")
    out = registry.call("file_analyze", Payload(modality=Modality.FILE, path=str(f)), {}, CallContext(actor="bob"))
    assert out.data and out.data["kind"] == "csv" and out.data["rows"] == 2 and out.data["columns"] == ["name", "score"]


def test_registry_rejects_duplicate_names(registry: ToolRegistry) -> None:
    with pytest.raises(ValueError):
        registry.register(registry.get("text_summarize"))

