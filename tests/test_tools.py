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


def test_image_generate_stub_is_a_real_svg_card(registry: ToolRegistry) -> None:
    ctx = CallContext(actor="bob")
    out = registry.call("image_generate", Payload(modality=Modality.TEXT, text="a blue landscape"), {}, ctx)
    assert out.modality is Modality.IMAGE
    assert out.data and out.data["format"] == "svg" and out.data["stub"] is True
    svg = out.data["content"]
    assert svg.lstrip().startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    assert 'fill="#eeeeee"' in svg  # grey card
    assert "STUB IMAGE" in svg
    assert "a blue landscape" in svg


def test_image_generate_stub_escapes_script_and_ampersand(registry: ToolRegistry) -> None:
    ctx = CallContext(actor="bob")
    prompt = 'evil <script>alert("x")</script> & <b>bold</b>'
    out = registry.call("image_generate", Payload(modality=Modality.TEXT, text=prompt), {}, ctx)
    svg = out.data["content"]
    # Raw markup and the ampersand must be escaped, never emitted verbatim.
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
    assert "alert(&quot;x&quot;)" in svg or "alert(" not in svg
    assert "&amp;" in svg
    assert "<b>" not in svg


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


# --- Regression tests for text_summarize instruction-prefix handling ---

THREE_FACT_INPUT = (
    "summarize the key points: The team shipped the beta on Monday. "
    "Three bugs were found in testing. The release is planned for Friday."
)


def test_text_summarize_preserves_all_three_facts(registry: ToolRegistry) -> None:
    out = registry.call("text_summarize", Payload(modality=Modality.TEXT, text=THREE_FACT_INPUT), {},
                        CallContext(actor="bob"))
    assert out.text is not None
    assert "beta on Monday" in out.text, "fact 1 missing: beta shipped Monday"
    assert "Three bugs" in out.text, "fact 2 missing: three bugs found during testing"
    assert "Friday" in out.text, "fact 3 missing: release planned for Friday"
    assert out.text.count("summarize") == 0, "instruction prefix leaked into summary"


def test_text_summarize_strips_instruction_prefix(registry: ToolRegistry) -> None:
    out = registry.call("text_summarize", Payload(modality=Modality.TEXT, text="summarize: The cat sat on the mat. The dog barked."),
                        {}, CallContext(actor="bob"))
    assert out.text is not None
    assert "summarize:" not in out.text
    assert "The cat sat on the mat." in out.text


def test_text_summarize_preserves_colon_in_content(registry: ToolRegistry) -> None:
    out = registry.call("text_summarize",
                        Payload(modality=Modality.TEXT, text="Beta launch: 2024. The team shipped on Monday. Three bugs found."),
                        {}, CallContext(actor="bob"))
    assert out.text is not None
    assert "Beta launch: 2024" in out.text, "colon in legitimate content was stripped"


def test_text_summarize_no_stale_output_between_requests(registry: ToolRegistry) -> None:
    first = registry.call("text_summarize",
                          Payload(modality=Modality.TEXT, text="The alpha release shipped Monday. Two bugs found."),
                          {}, CallContext(actor="bob"))
    second = registry.call("text_summarize",
                           Payload(modality=Modality.TEXT, text="The beta release shipped Tuesday. Five bugs found."),
                           {}, CallContext(actor="bob"))
    assert first.text is not None and second.text is not None
    assert "Tuesday" in second.text and "Five bugs" in second.text
    assert "Monday" not in second.text and "Two bugs" not in second.text


def test_text_summarize_invalid_input_raises_rather_than_succeeds(registry: ToolRegistry) -> None:
    # An instruction prefix with no content must not silently return success.
    with pytest.raises(MissingInputError):
        registry.call("text_summarize", Payload(modality=Modality.TEXT, text="summarize:"), {},
                      CallContext(actor="bob"))



def test_image_provider_error_retryability_and_sanitization() -> None:
    from agent_repo.tools import _is_retryable_provider_error, _sanitize_provider_error

    class CustomHTTPError(Exception):
        def __init__(self, code: int, msg: str):
            super().__init__(msg)
            self.code = code

    # Non-retryable permanent errors
    for code in (400, 401, 402, 403, 404, 410, 429):
        err = CustomHTTPError(code, f"HTTP {code} Error")
        assert not _is_retryable_provider_error(err), f"Expected HTTP {code} to be non-retryable"

    # Retryable transient errors
    for code in (500, 502, 503, 504):
        err = CustomHTTPError(code, f"HTTP {code} Gateway / Internal Error")
        assert _is_retryable_provider_error(err), f"Expected HTTP {code} to be retryable"
    assert _is_retryable_provider_error(TimeoutError("request timed out"))

    # Sanitization suppresses prompts and file paths
    dirty = "Error with prompt 'generate an image of a blue dragon' at C:\\Users\\Prakhar\\secret.key"
    cleaned = _sanitize_provider_error(dirty, prompt="generate an image of a blue dragon", prefix="")
    assert "generate an image of a blue dragon" not in cleaned
    assert "[REDACTED_PROMPT]" in cleaned
    assert "secret.key" not in cleaned


def test_pollinations_flux_bounds_response_size(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_repo.tools import _pollinations_flux, ToolError
    import urllib.request
    import io

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    # Return 200 bytes when max_bytes is 100
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=30: FakeResponse(b"x" * 200))

    with pytest.raises(ToolError, match="exceeded size limit"):
        _pollinations_flux("test prompt", max_bytes=100)
