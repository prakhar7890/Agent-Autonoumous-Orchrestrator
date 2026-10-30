"""Offline tests for the opt-in real tool providers (Groq ASR, Gemini vision).

No network is used: the provider client builders are module-level functions that these tests
replace with fakes, so the real code paths (payload shaping, JSON parsing, error wrapping) are
exercised while the network call is stubbed.
"""

from __future__ import annotations

import base64
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

import pytest

import agent_repo.tools as tools
from agent_repo.config import Settings, ToolSettings, merged_env
from agent_repo.manifest import Modality
from agent_repo.tools import (
    CallContext, MissingInputError, Payload, ToolError, _parse_labels, audio_transcribe_groq,
    audio_transcribe_stub, builtin_registry, image_classify_gemini, image_classify_stub,
    image_generate_gemini, image_generate_huggingface, image_generate_stub,
)

MISSING_ENV_FILE = Path("no-such-file.env")


def _audio(tmp_path: Path) -> Payload:
    f = tmp_path / "clip.wav"
    f.write_bytes(b"RIFF0000WAVEfmt ")
    return Payload(modality=Modality.AUDIO, path=str(f))


def _image(tmp_path: Path) -> Payload:
    f = tmp_path / "photo.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    return Payload(modality=Modality.IMAGE, path=str(f))


def _fake_groq(reply: str | Exception, captured: dict[str, Any] | None = None) -> Any:
    def client(api_key: str) -> Any:
        def create(**kwargs: Any) -> Any:
            if captured is not None:
                captured["kwargs"] = kwargs
            if isinstance(reply, Exception):
                raise reply
            return SimpleNamespace(text=reply)
        return SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
    return client


def _fake_gemini(reply: str | Exception) -> Any:
    def client(api_key: str) -> Any:
        def generate_content(**kwargs: Any) -> Any:
            if isinstance(reply, Exception):
                raise reply
            return SimpleNamespace(text=reply)
        return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    return client


# ---------------------------------------------------------------- configuration

def test_tool_settings_default_to_stubs() -> None:
    settings = Settings.from_env({}, env_file=MISSING_ENV_FILE)
    assert settings.tools == ToolSettings(transcriber="stub", image_classifier="stub")


def test_tool_settings_read_from_env() -> None:
    settings = Settings.from_env(
        {"AGENT_REPO_TRANSCRIBER": "Groq", "AGENT_REPO_IMAGE_CLASSIFIER": "GEMINI"},
        env_file=MISSING_ENV_FILE,
    )
    assert settings.tools.transcriber == "groq"
    assert settings.tools.image_classifier == "gemini"


def test_tool_settings_read_image_generator_from_env() -> None:
    settings = Settings.from_env(
        {"AGENT_REPO_IMAGE_GENERATOR": "gemini"}, env_file=MISSING_ENV_FILE
    )
    assert settings.tools.image_generator == "gemini"


@pytest.mark.parametrize("bad", [
    {"AGENT_REPO_TRANSCRIBER": "whisper"},
    {"AGENT_REPO_IMAGE_CLASSIFIER": "clip"},
])
def test_unknown_tool_provider_is_rejected(bad: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        Settings.from_env(bad, env_file=MISSING_ENV_FILE)


def test_merged_env_excludes_neo4j_test_wipe(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("NEO4J_TEST_WIPE=1\nAGENT_REPO_HASH_SALT=abc\n", encoding="utf-8")
    merged = merged_env({}, env_file)
    assert "NEO4J_TEST_WIPE" not in merged
    assert merged["AGENT_REPO_HASH_SALT"] == "abc"


# ---------------------------------------------------------------- registry selection

def test_default_registry_uses_stubs() -> None:
    reg = builtin_registry()
    asr, classifier = reg.get("audio_transcribe"), reg.get("image_classify")
    assert asr.is_stub and asr.fn is audio_transcribe_stub
    assert classifier.is_stub and classifier.fn is image_classify_stub


def test_registry_selects_real_providers() -> None:
    reg = builtin_registry(tool_settings=ToolSettings(transcriber="groq", image_classifier="gemini"))
    asr, classifier = reg.get("audio_transcribe"), reg.get("image_classify")
    assert not asr.is_stub and asr.fn is tools.audio_transcribe_groq
    assert not classifier.is_stub and classifier.fn is tools.image_classify_gemini
    assert "STUB" not in asr.description and "STUB" not in classifier.description
    assert reg.get("image_generate").is_stub and reg.get("email_send").is_stub


def test_registry_calls_real_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "merged_env", lambda: {"GROQ_API_KEY": "k"})
    monkeypatch.setattr(tools, "_groq_transcription_client", _fake_groq("hello world"))
    reg = builtin_registry(tool_settings=ToolSettings(transcriber="groq"))
    out = reg.call("audio_transcribe", _audio(tmp_path), {}, CallContext(actor="bob"))
    assert out.data is not None and out.data["stub"] is False
    assert reg.audit_log[-1].outcome == "ok"


# ---------------------------------------------------------------- Groq ASR

def test_audio_transcribe_groq_returns_transcript(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(tools, "merged_env", lambda: {"GROQ_API_KEY": "k"})
    monkeypatch.setattr(tools, "_groq_transcription_client", _fake_groq("  We ship on Friday.  ", captured))
    out = audio_transcribe_groq(_audio(tmp_path), {})
    assert out.modality is Modality.TEXT
    assert out.text == "We ship on Friday."
    assert out.data == {"provider": "groq", "model": "whisper-large-v3", "stub": False}
    assert captured["kwargs"]["model"] == "whisper-large-v3"


def test_audio_transcribe_groq_model_can_be_overridden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(tools, "merged_env", lambda: {"GROQ_API_KEY": "k"})
    monkeypatch.setattr(tools, "_groq_transcription_client", _fake_groq("hi", captured))
    audio_transcribe_groq(_audio(tmp_path), {"model": "whisper-large-v3-turbo"})
    assert captured["kwargs"]["model"] == "whisper-large-v3-turbo"


def test_audio_transcribe_groq_without_key_is_tool_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "merged_env", lambda: {})
    with pytest.raises(ToolError, match="GROQ_API_KEY"):
        audio_transcribe_groq(_audio(tmp_path), {})


def test_audio_transcribe_groq_wraps_provider_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "merged_env", lambda: {"GROQ_API_KEY": "k"})
    monkeypatch.setattr(tools, "_groq_transcription_client", _fake_groq(RuntimeError("503 unavailable")))
    with pytest.raises(ToolError, match="503 unavailable"):
        audio_transcribe_groq(_audio(tmp_path), {})


# ---------------------------------------------------------------- Gemini vision

def test_image_classify_gemini_parses_and_truncates_labels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reply = ('{"labels":[{"label":"dog","score":0.91},{"label":"grass","score":0.4},'
             '{"label":"extra","score":0.1}]}')
    monkeypatch.setattr(tools, "merged_env", lambda: {"GOOGLE_API_KEY": "k"})
    monkeypatch.setattr(tools, "_gemini_client", _fake_gemini(reply))
    out = image_classify_gemini(_image(tmp_path), {"top_k": 2})
    assert out.modality is Modality.JSON
    assert out.data is not None
    assert out.data["labels"] == [{"label": "dog", "score": 0.91}, {"label": "grass", "score": 0.4}]
    assert out.data["stub"] is False and out.data["provider"] == "gemini"
    assert out.text == "dog (0.91) grass (0.4)"


def test_image_classify_gemini_without_key_is_tool_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "merged_env", lambda: {})
    with pytest.raises(ToolError, match="GOOGLE_API_KEY"):
        image_classify_gemini(_image(tmp_path), {})


def test_image_classify_gemini_missing_sdk_names_providers_extra_without_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. Real Part.from_bytes path when google.genai.types is importable
    called_from_bytes: dict[str, Any] = {}
    fake_part_cls = SimpleNamespace(
        from_bytes=lambda *, data, mime_type: called_from_bytes.update(
            {"data": data, "mime_type": mime_type}
        ) or ("real_part", data, mime_type)
    )
    fake_genai_mod = SimpleNamespace(types=SimpleNamespace(Part=fake_part_cls))
    fake_google_mod = SimpleNamespace(genai=fake_genai_mod)
    with monkeypatch.context() as mp:
        mp.setitem(sys.modules, "google", fake_google_mod)
        mp.setitem(sys.modules, "google.genai", fake_genai_mod)
        part_real = tools._gemini_image_part(b"png-bytes", "image/png")
    assert part_real == ("real_part", b"png-bytes", "image/png")
    assert called_from_bytes == {"data": b"png-bytes", "mime_type": "image/png"}

    # 2. Missing SDK: fallback image part + clear ToolError naming providers extra without secrets
    sentinel_secret = "SECRET-GEMINI-KEY-VALUE-999"
    monkeypatch.setattr(
        tools,
        "merged_env",
        lambda: {"GOOGLE_API_KEY": sentinel_secret, "AGENT_REPO_IMAGE_CLASSIFIER": "gemini"},
    )
    monkeypatch.setitem(sys.modules, "google", None)
    monkeypatch.setitem(sys.modules, "google.genai", None)
    assert tools._gemini_image_part(b"png-bytes", "image/png") == {
        "data": b"png-bytes",
        "mime_type": "image/png",
    }
    reg = builtin_registry(tool_settings=ToolSettings(image_classifier="gemini"))
    with pytest.raises(ToolError) as exc_info:
        reg.call("image_classify", _image(tmp_path), {}, CallContext(actor="alice"))
    msg = str(exc_info.value)
    assert "google-genai" in msg
    assert "providers" in msg
    assert sentinel_secret not in msg
    assert exc_info.value.__cause__ is None and exc_info.value.__suppress_context__ is True


def test_image_classify_gemini_wraps_non_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "merged_env", lambda: {"GOOGLE_API_KEY": "k"})
    monkeypatch.setattr(tools, "_gemini_client", _fake_gemini("I think it is a dog."))
    with pytest.raises(ToolError, match="non-JSON"):
        image_classify_gemini(_image(tmp_path), {})


def test_parse_labels_handles_code_fences_and_string_scores() -> None:
    fenced = '```json\n{"labels":[{"label":"cat","score":"0.5"}]}\n```'
    assert _parse_labels(fenced, 1) == [{"label": "cat", "score": 0.5}]


@pytest.mark.parametrize("reply", ["I see a dog", '{"labels":[]}', '{"other":1}'])
def test_parse_labels_rejects_unusable_output(reply: str) -> None:
    with pytest.raises(ToolError):
        _parse_labels(reply, 2)


def _fake_gemini_generate_content(parts: list[Any] | Exception | None) -> Any:
    """Fake client for client.models.generate_content. Returns a response whose parts carry
    inline_data (Blob) — the new response shape; never calls generate_images."""
    call_count = {"n": 0}

    def client(api_key: str) -> Any:
        def generate_content(**kwargs: Any) -> Any:
            call_count["n"] += 1
            if isinstance(parts, Exception):
                raise parts
            if parts is None:
                return SimpleNamespace(candidates=[])
            content = SimpleNamespace(parts=parts)
            candidate = SimpleNamespace(content=content)
            return SimpleNamespace(candidates=[candidate])
        return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))

    client.call_count = call_count
    return client


def _inline_data_part(data: bytes | None, mime_type: str) -> Any:
    return SimpleNamespace(inline_data=SimpleNamespace(data=data, mime_type=mime_type))


# ---------------------------------------------------------------- Gemini image generation

def test_image_generate_gemini_returns_real_image_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "merged_env", lambda: {"GOOGLE_API_KEY": "k"})
    png_bytes = b"\x89PNG\r\n\x1a\nfake-png-bytes"
    monkeypatch.setattr(tools, "_gemini_images_client", _fake_gemini_generate_content([_inline_data_part(png_bytes, "image/png")]))
    out = image_generate_gemini(
        Payload(modality=Modality.TEXT, text="a blue landscape"),
        {"model": "gemini-nano-banana-2.1"},
    )
    assert out.modality is Modality.IMAGE
    assert out.data is not None
    assert out.data["format"] == "png"
    assert out.data["stub"] is False
    assert out.data["provider"] == "gemini"
    assert out.data["model"] == "gemini-nano-banana-2.1"
    assert out.data["content"] == png_bytes
    assert "[image/png" in out.text and "bytes" in out.text


def test_image_generate_gemini_uses_generate_content_not_generate_images(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prove generate_images is no longer called; only generate_content is used."""
    monkeypatch.setattr(tools, "merged_env", lambda: {"GOOGLE_API_KEY": "k"})
    called = {"generate_images": 0, "generate_content": 0}

    def client(api_key: str) -> Any:
        def generate_images(**kwargs: Any) -> Any:
            called["generate_images"] += 1
            raise AssertionError("generate_images must not be called")

        def generate_content(**kwargs: Any) -> Any:
            called["generate_content"] += 1
            png_bytes = b"\x89PNG\r\n\x1a\nfake-png-bytes"
            content = SimpleNamespace(parts=[SimpleNamespace(
                inline_data=SimpleNamespace(data=png_bytes, mime_type="image/png"))])
            return SimpleNamespace(candidates=[SimpleNamespace(content=content)])
        return SimpleNamespace(models=SimpleNamespace(
            generate_images=generate_images, generate_content=generate_content))

    monkeypatch.setattr(tools, "_gemini_images_client", client)
    image_generate_gemini(
        Payload(modality=Modality.TEXT, text="a cat"),
        {"model": "gemini-nano-banana-2.1"},
    )
    assert called["generate_images"] == 0
    assert called["generate_content"] == 1


def test_image_generate_gemini_without_key_is_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "merged_env", lambda: {})
    with pytest.raises(ToolError, match="GOOGLE_API_KEY"):
        image_generate_gemini(Payload(modality=Modality.TEXT, text="a cat"), {})


def test_image_generate_gemini_missing_prompt_is_missing_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "merged_env", lambda: {"GOOGLE_API_KEY": "k"})
    with pytest.raises(MissingInputError, match="no prompt"):
        image_generate_gemini(Payload(modality=Modality.TEXT, text=""), {})


def test_image_generate_gemini_provider_error_wraps_as_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "merged_env", lambda: {"GOOGLE_API_KEY": "k"})
    monkeypatch.setattr(tools, "_gemini_images_client", _fake_gemini_generate_content(RuntimeError("rate limit exceeded")))
    with pytest.raises(ToolError, match="rate limit exceeded"):
        image_generate_gemini(Payload(modality=Modality.TEXT, text="a cat"), {})


def test_image_generate_gemini_non_image_bytes_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "merged_env", lambda: {"GOOGLE_API_KEY": "k"})
    monkeypatch.setattr(tools, "_gemini_images_client", _fake_gemini_generate_content([_inline_data_part(b"<svg>not an image</svg>", "image/svg+xml")]))
    with pytest.raises(ToolError, match="provider returned no image"):
        image_generate_gemini(Payload(modality=Modality.TEXT, text="a cat"), {})


def test_image_generate_gemini_oversize_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    big_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * (5 * 1024 * 1024)  # 5 MB PNG
    monkeypatch.setattr(tools, "merged_env", lambda: {"GOOGLE_API_KEY": "k", "AGENT_REPO_API_MAX_IMAGE_BYTES": "4194304"})
    monkeypatch.setattr(tools, "_gemini_images_client", _fake_gemini_generate_content([_inline_data_part(big_png, "image/png")]))
    with pytest.raises(ToolError, match="exceeds size cap"):
        image_generate_gemini(Payload(modality=Modality.TEXT, text="a cat"), {})


def test_image_generate_gemini_refusal_returns_clean_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "merged_env", lambda: {"GOOGLE_API_KEY": "k"})
    monkeypatch.setattr(tools, "_gemini_images_client", _fake_gemini_generate_content(None))
    with pytest.raises(ToolError, match="provider returned no image"):
        image_generate_gemini(Payload(modality=Modality.TEXT, text="a cat"), {})


def test_image_generate_gemini_quota_error_one_call_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "merged_env", lambda: {"GOOGLE_API_KEY": "k"})
    client = _fake_gemini_generate_content(RuntimeError("quota exceeded"))
    monkeypatch.setattr(tools, "_gemini_images_client", client)
    with pytest.raises(ToolError, match="quota exceeded"):
        image_generate_gemini(Payload(modality=Modality.TEXT, text="a cat"), {})
    assert client.call_count["n"] == 1


def test_registry_selects_gemini_image_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "merged_env", lambda: {"GOOGLE_API_KEY": "k"})
    png_bytes = b"\x89PNG\r\n\x1a\nfake-png-bytes"
    monkeypatch.setattr(tools, "_gemini_images_client", _fake_gemini_generate_content([_inline_data_part(png_bytes, "image/png")]))
    reg = builtin_registry(tool_settings=ToolSettings(image_generator="gemini"))
    gen = reg.get("image_generate")
    assert not gen.is_stub
    assert gen.fn is image_generate_gemini
    assert "STUB" not in gen.description
    out = reg.call("image_generate", Payload(modality=Modality.TEXT, text="a cat"), {}, CallContext(actor="bob"))
    assert out.modality is Modality.IMAGE
    assert out.data is not None and out.data["stub"] is False
    assert reg.audit_log[-1].outcome == "ok"


def test_default_registry_still_uses_stub_for_image_generate() -> None:
    reg = builtin_registry()
    gen = reg.get("image_generate")
    assert gen.is_stub and gen.fn is image_generate_stub


# ---------------------------------------------------------------- shared contract

def test_real_tools_still_require_a_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "merged_env", lambda: {"GROQ_API_KEY": "k", "GOOGLE_API_KEY": "k"})
    with pytest.raises(MissingInputError):
        audio_transcribe_groq(Payload(modality=Modality.AUDIO), {})
    with pytest.raises(MissingInputError):
        image_classify_gemini(Payload(modality=Modality.IMAGE), {})


def test_image_generate_gemini_missing_sdk_names_providers_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel_secret = "SECRET-GEMINI-KEY-VALUE-999"
    monkeypatch.setattr(
        tools,
        "merged_env",
        lambda: {"GOOGLE_API_KEY": sentinel_secret, "AGENT_REPO_IMAGE_GENERATOR": "gemini"},
    )
    monkeypatch.setitem(sys.modules, "google", None)
    monkeypatch.setitem(sys.modules, "google.genai", None)
    reg = builtin_registry(tool_settings=ToolSettings(image_generator="gemini"))
    with pytest.raises(ToolError) as exc_info:
        reg.call("image_generate", Payload(modality=Modality.TEXT, text="a cat"), {}, CallContext(actor="alice"))
    msg = str(exc_info.value)
    assert "google-genai" in msg
    assert "providers" in msg
    assert sentinel_secret not in msg
    assert exc_info.value.__cause__ is None and exc_info.value.__suppress_context__ is True


def test_image_generate_gemini_prompt_never_in_error(monkeypatch: pytest.MonkeyPatch) -> None:
    secret_prompt = "secret-project-alpha-confidential-prompt"
    monkeypatch.setattr(tools, "merged_env", lambda: {"GOOGLE_API_KEY": "k"})
    monkeypatch.setattr(tools, "_gemini_images_client", _fake_gemini_generate_content(RuntimeError("boom")))
    try:
        image_generate_gemini(Payload(modality=Modality.TEXT, text=secret_prompt), {})
    except ToolError as exc:
        assert secret_prompt not in str(exc)
    else:
        raise AssertionError("expected ToolError")


def test_config_reads_gemini_image_model_from_env() -> None:
    settings = Settings.from_env(
        {"AGENT_REPO_GEMINI_IMAGE_MODEL": "gemini-3.1-flash-image"}, env_file=MISSING_ENV_FILE
    )
    assert settings.tools.image_generator_model == "gemini-3.1-flash-image"


def test_config_default_model_is_nano_banana() -> None:
    settings = Settings.from_env({}, env_file=MISSING_ENV_FILE)
    assert settings.tools.image_generator_model == "gemini-nano-banana-2.1"


def test_config_reads_max_image_bytes_from_env() -> None:
    settings = Settings.from_env(
        {"AGENT_REPO_API_MAX_IMAGE_BYTES": "2097152"}, env_file=MISSING_ENV_FILE
    )
    assert settings.tools.max_image_bytes == 2 * 1024 * 1024


def test_config_max_image_bytes_must_be_positive() -> None:
    with pytest.raises(ValueError, match="must be a positive integer"):
        Settings.from_env({"AGENT_REPO_API_MAX_IMAGE_BYTES": "0"}, env_file=MISSING_ENV_FILE)


def test_non_retryable_tool_error_runs_once_and_surfaces_detail() -> None:
    from agent_repo.api import support, views
    from agent_repo.config import ExecutionLimits
    from agent_repo.execution.runner import Runner
    from agent_repo.graph.store import InMemoryGraph
    from agent_repo.manifest import AgentManifest, PipelineStep, RiskTier
    from agent_repo.repository import AgentRepository
    from agent_repo.routing import BreakReason
    from agent_repo.seeds import seed_repository
    from agent_repo.tools import ToolSpec

    calls = {"n": 0}

    def quota_tool(p: Payload, params: dict[str, Any]) -> Payload:
        calls["n"] += 1
        raise ToolError("gemini image generation failed: 429 RESOURCE_EXHAUSTED", retryable=False)

    reg = builtin_registry()
    reg.register(ToolSpec("quota_gen", "test quota tool", RiskTier.LOW, (Modality.TEXT,), Modality.IMAGE, False, quota_tool))
    repo = AgentRepository(InMemoryGraph(), tools=reg)
    seed_repository(repo, "alice")
    m = AgentManifest(
        name="quota_image_agent",
        purpose="Render a custom diagram from a text description.",
        usefulness="Creates custom diagrams.",
        input_modalities=[Modality.TEXT],
        input_description="diagram prompt",
        processing_description="calls quota_gen",
        pipeline=[PipelineStep(kind="tool", name="quota_gen")],
        output_type=Modality.IMAGE,
        output_description="diagram image",
        example_queries=["render a custom diagram from this description"],
    )
    rec = repo.create_agent(m, author="alice")
    repo.submit_for_approval(rec.agent, rec.version, "alice")
    repo.approve(rec.agent, rec.version, "alice")

    runner = Runner(repo, limits=ExecutionLimits(max_retries_per_tool=3))
    result = runner.execute_query("render a custom diagram from this description")
    assert calls["n"] == 1
    assert result.status == "broken"
    assert result.break_reason is BreakReason.MAX_RETRIES
    assert "429 RESOURCE_EXHAUSTED" in result.user_message
    assert "429 RESOURCE_EXHAUSTED" in result.clarifying_question

    settings = Settings()
    explained = support.explain_text(repo, result.execution_id, settings)
    assert "429 RESOURCE_EXHAUSTED" in explained
    api_resp = views.ask_response(repo, result, settings)
    assert "429 RESOURCE_EXHAUSTED" in api_resp.user_message
    assert "429 RESOURCE_EXHAUSTED" in api_resp.clarifying_question


def test_retryable_tool_error_still_retries_configured_times(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_repo.config import ExecutionLimits
    from agent_repo.execution.runner import Runner
    from agent_repo.graph.store import InMemoryGraph
    from agent_repo.manifest import AgentManifest, PipelineStep, RiskTier
    from agent_repo.repository import AgentRepository
    from agent_repo.routing import BreakReason
    from agent_repo.seeds import seed_repository
    from agent_repo.tools import ToolSpec

    calls = {"n": 0}

    def transient_tool(p: Payload, params: dict[str, Any]) -> Payload:
        calls["n"] += 1
        raise ToolError("503 SERVICE_UNAVAILABLE")

    reg = builtin_registry()
    reg.register(ToolSpec("transient_gen", "test transient tool", RiskTier.LOW, (Modality.TEXT,), Modality.TEXT, False, transient_tool))
    repo = AgentRepository(InMemoryGraph(), tools=reg)
    seed_repository(repo, "alice")
    m = AgentManifest(
        name="transient_agent",
        purpose="Render a custom diagram from a text description.",
        usefulness="Creates custom diagrams.",
        input_modalities=[Modality.TEXT],
        input_description="text",
        processing_description="calls transient_gen",
        pipeline=[PipelineStep(kind="tool", name="transient_gen")],
        output_type=Modality.TEXT,
        output_description="text",
        example_queries=["render a custom diagram from this description"],
    )
    rec = repo.create_agent(m, author="alice")
    repo.submit_for_approval(rec.agent, rec.version, "alice")
    repo.approve(rec.agent, rec.version, "alice")

    runner = Runner(repo, limits=ExecutionLimits(max_retries_per_tool=2))
    result = runner.execute_query("render a custom diagram from this description")
    assert calls["n"] == 3
    assert result.break_reason is BreakReason.MAX_RETRIES

    monkeypatch.setattr(tools, "merged_env", lambda: {"GOOGLE_API_KEY": "k"})
    for permanent_code in (400, 401, 403, 404, 429):
        monkeypatch.setattr(
            tools,
            "_gemini_images_client",
            _fake_gemini_generate_content(RuntimeError(f"{permanent_code} permanent failure")),
        )
        with pytest.raises(ToolError) as exc_info:
            image_generate_gemini(Payload(modality=Modality.TEXT, text="a cat"), {})
        assert exc_info.value.retryable is False

    for retryable_msg in ("500 INTERNAL", "503 UNAVAILABLE", "timed out waiting for upstream"):
        monkeypatch.setattr(
            tools,
            "_gemini_images_client",
            _fake_gemini_generate_content(RuntimeError(retryable_msg)),
        )
        with pytest.raises(ToolError) as exc_info:
            image_generate_gemini(Payload(modality=Modality.TEXT, text="a cat"), {})
        assert exc_info.value.retryable is True


def test_breakpoint_detail_scrubs_aiza_key_prompt_and_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_repo.api import support, views
    from agent_repo.config import ExecutionLimits
    from agent_repo.execution.runner import Runner
    from agent_repo.graph.store import InMemoryGraph
    from agent_repo.manifest import AgentManifest, PipelineStep, RiskTier
    from agent_repo.repository import AgentRepository
    from agent_repo.seeds import seed_repository
    from agent_repo.tools import ToolSpec

    fake_key = "AIzaSyFakeKey1234567890123456789012345"
    secret_prompt = "render a custom diagram of secret-cobalt-flamingo"
    secret_path = r"C:\Users\alice\secret_folder\diagram.png"

    def leaky_tool(p: Payload, params: dict[str, Any]) -> Payload:
        raise ToolError(
            f"429 RESOURCE_EXHAUSTED key={fake_key} prompt={p.text} file={secret_path}",
            retryable=False,
        )

    reg = builtin_registry()
    reg.register(ToolSpec("leaky_gen", "test leaky tool", RiskTier.LOW, (Modality.TEXT,), Modality.IMAGE, False, leaky_tool))
    repo = AgentRepository(InMemoryGraph(), tools=reg)
    seed_repository(repo, "alice")
    m = AgentManifest(
        name="leaky_image_agent",
        purpose="Render a custom diagram from a text description.",
        usefulness="Creates custom diagrams.",
        input_modalities=[Modality.TEXT],
        input_description="diagram prompt",
        processing_description="calls leaky_gen",
        pipeline=[PipelineStep(kind="tool", name="leaky_gen")],
        output_type=Modality.IMAGE,
        output_description="diagram image",
        example_queries=["render a custom diagram"],
    )
    rec = repo.create_agent(m, author="alice")
    repo.submit_for_approval(rec.agent, rec.version, "alice")
    repo.approve(rec.agent, rec.version, "alice")

    runner = Runner(repo, limits=ExecutionLimits(max_retries_per_tool=3))
    result = runner.execute_query(
        "render a custom diagram",
        payload=Payload(modality=Modality.TEXT, text=secret_prompt),
    )
    settings = Settings()
    explained = support.explain_text(repo, result.execution_id, settings)
    api_resp = views.ask_response(repo, result, settings)

    combined = "\n".join([
        result.user_message,
        result.clarifying_question,
        explained,
        api_resp.user_message,
        api_resp.clarifying_question,
    ])
    assert "429 RESOURCE_EXHAUSTED" in combined
    assert fake_key not in combined
    assert "secret-cobalt-flamingo" not in combined
    assert secret_path not in combined


# ---------------------------------------------------------------- Hugging Face open-source image generation

def test_image_generate_huggingface_returns_real_image_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    png_bytes = b"\x89PNG\r\n\x1a\nfake-hf-png-bytes"

    def fake_post(model: str, api_key: str, prompt: str) -> bytes:
        captured.update({"model": model, "api_key": api_key, "prompt": prompt})
        return png_bytes

    monkeypatch.setattr(tools, "merged_env", lambda: {"HF_TOKEN": "hf_fake_token"})
    monkeypatch.setattr(tools, "_hf_post_image", fake_post)
    out = image_generate_huggingface(Payload(modality=Modality.TEXT, text="a mountain lake"), {})
    assert out.modality is Modality.IMAGE
    assert out.data is not None
    assert out.data["format"] == "png"
    assert out.data["stub"] is False
    assert out.data["provider"] == "huggingface"
    assert out.data["model"] == "black-forest-labs/FLUX.1-schnell"
    assert out.data["content"] == png_bytes
    assert captured == {
        "model": "black-forest-labs/FLUX.1-schnell",
        "api_key": "hf_fake_token",
        "prompt": "a mountain lake",
    }


def test_image_generate_huggingface_works_without_token_or_credits(monkeypatch: pytest.MonkeyPatch) -> None:
    png_bytes = b"\x89PNG\r\n\x1a\nfake-free-png-bytes"
    monkeypatch.setattr(tools, "merged_env", lambda: {})
    monkeypatch.setattr(tools, "_hf_post_image", lambda m, k, p: png_bytes)
    out = image_generate_huggingface(Payload(modality=Modality.TEXT, text="a red car"), {})
    assert out.modality is Modality.IMAGE
    assert out.data is not None
    assert out.data["stub"] is False
    assert out.data["content"] == png_bytes


def test_image_generate_huggingface_error_classification_and_scrubbing(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_hf_token = "hf_SecretTokenValue1234567890ABCD"
    secret_prompt = "secret-emerald-falcon-diagram"
    monkeypatch.setattr(tools, "merged_env", lambda: {"HF_TOKEN": fake_hf_token})

    def fail_429(model: str, api_key: str, prompt: str) -> bytes:
        raise RuntimeError(f"429 Too Many Requests token={fake_hf_token} prompt={prompt}")

    monkeypatch.setattr(tools, "_hf_post_image", fail_429)
    with pytest.raises(ToolError) as exc_info:
        image_generate_huggingface(Payload(modality=Modality.TEXT, text=secret_prompt), {})
    assert exc_info.value.retryable is False
    assert fake_hf_token not in str(exc_info.value)
    assert secret_prompt not in str(exc_info.value)
    assert "429" in str(exc_info.value)

    def fail_503(model: str, api_key: str, prompt: str) -> bytes:
        raise RuntimeError("503 Model is currently loading")

    monkeypatch.setattr(tools, "_hf_post_image", fail_503)
    with pytest.raises(ToolError) as exc_info_503:
        image_generate_huggingface(Payload(modality=Modality.TEXT, text=secret_prompt), {})
    assert exc_info_503.value.retryable is True


def test_registry_selects_huggingface_image_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    png_bytes = b"\x89PNG\r\n\x1a\nfake-hf-png-bytes"
    monkeypatch.setattr(tools, "merged_env", lambda: {"HF_TOKEN": "hf_fake_token"})
    monkeypatch.setattr(tools, "_hf_post_image", lambda m, k, p: png_bytes)
    for alias in ("huggingface", "hf"):
        reg = builtin_registry(tool_settings=ToolSettings(image_generator=alias))
        gen = reg.get("image_generate")
        assert not gen.is_stub
        assert gen.fn is image_generate_huggingface
        out = reg.call("image_generate", Payload(modality=Modality.TEXT, text="a cat"), {}, CallContext(actor="bob"))
        assert out.modality is Modality.IMAGE
        assert out.data is not None and out.data["provider"] == "huggingface" and out.data["stub"] is False


def test_image_generate_huggingface_webp_and_jpeg_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "merged_env", lambda: {})
    webp_bytes = b"RIFF\x20\x00\x00\x00WEBPfake-webp-payload"
    monkeypatch.setattr(tools, "_hf_post_image", lambda m, k, p: webp_bytes)
    out_webp = image_generate_huggingface(Payload(modality=Modality.TEXT, text="a red car"), {})
    assert out_webp.data is not None
    assert out_webp.data["format"] == "webp"
    assert out_webp.data["mime"] == "image/webp"
    assert out_webp.data["stub"] is False
    assert out_webp.data["content"] == webp_bytes

    jpeg_bytes = b"\xff\xd8\xff\xe0fake-jpeg-payload"
    monkeypatch.setattr(tools, "_hf_post_image", lambda m, k, p: jpeg_bytes)
    out_jpeg = image_generate_huggingface(Payload(modality=Modality.TEXT, text="a red car"), {})
    assert out_jpeg.data is not None
    assert out_jpeg.data["format"] == "jpeg"
    assert out_jpeg.data["mime"] == "image/jpeg"
    assert out_jpeg.data["stub"] is False
    assert out_jpeg.data["content"] == jpeg_bytes


def test_image_generate_huggingface_missing_prompt_and_validation_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "merged_env", lambda: {"AGENT_REPO_API_MAX_IMAGE_BYTES": "64"})
    with pytest.raises(MissingInputError, match="no prompt"):
        image_generate_huggingface(Payload(modality=Modality.TEXT, text="   "), {})

    monkeypatch.setattr(tools, "_hf_post_image", lambda m, k, p: b"not-an-image")
    with pytest.raises(ToolError, match="provider returned no image"):
        image_generate_huggingface(Payload(modality=Modality.TEXT, text="a car"), {})

    monkeypatch.setattr(tools, "_hf_post_image", lambda m, k, p: b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    with pytest.raises(ToolError, match="exceeds size cap"):
        image_generate_huggingface(Payload(modality=Modality.TEXT, text="a car"), {})


def test_hf_post_image_fallback_order_and_accurate_provider_attribution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "merged_env", lambda: {})
    webp_bytes = b"RIFF\x20\x00\x00\x00WEBPspace-bytes"
    jpeg_bytes = b"\xff\xd8\xff\xe0fallback-bytes"

    monkeypatch.setattr(tools, "_hf_space_flux_schnell", lambda prompt, api_key="": webp_bytes)
    monkeypatch.setattr(tools, "_pollinations_flux", lambda prompt: jpeg_bytes)
    out_primary = image_generate_huggingface(Payload(modality=Modality.TEXT, text="a car"), {})
    assert out_primary.data is not None
    assert out_primary.data["content"] == webp_bytes
    assert out_primary.data["provider"] == "huggingface"
    assert out_primary.data["model"] == "black-forest-labs/FLUX.1-schnell"
    assert "huggingface:black-forest-labs/FLUX.1-schnell" in (out_primary.text or "")

    def fail_space(prompt: str, api_key: str = "") -> bytes:
        raise RuntimeError("ZeroGPU queue full")

    monkeypatch.setattr(tools, "_hf_space_flux_schnell", fail_space)
    out_fallback = image_generate_huggingface(Payload(modality=Modality.TEXT, text="a car"), {})
    assert out_fallback.data is not None
    assert out_fallback.data["content"] == jpeg_bytes
    assert out_fallback.data["provider"] == "pollinations"
    assert out_fallback.data["model"] == "flux"
    assert "pollinations:flux" in (out_fallback.text or "")
    assert "huggingface" not in (out_fallback.text or "")




