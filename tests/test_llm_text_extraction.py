"""Phase tests: normalise LangChain structured content blocks to plain text.

`LangChainLLM.generate` / `draft_agent` must yield plain text even when a provider returns
`AIMessage.content` as a list of content blocks (as Gemini 3.x does). These tests are fully
offline: they inject a fake chat model instead of calling a live provider.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage

from agent_repo.llm import LangChainLLM, extract_text
from agent_repo.manifest import Modality


class _BlockChatModel:
    """Minimal duck-typed chat model returning a chosen `AIMessage.content` from `invoke`."""

    def __init__(self, content: Any) -> None:
        self._content = content
        self.calls: list[Any] = []

    def invoke(self, messages: list[Any], **kwargs: Any) -> AIMessage:
        self.calls.append(messages)
        return AIMessage(content=self._content)


# ------------------------------------------------------------------ extract_text unit tests

@pytest.mark.parametrize(("content", "expected"), [
    ("plain text", "plain text"),
    (None, ""),
    ([], ""),
    (["a", "b"], "ab"),
    ([{"type": "text", "text": "READY"}], "READY"),
    ([{"type": "text", "text": "Hello "}, {"type": "text", "text": "world"}], "Hello world"),
    ([{"type": "text", "text": {"value": "nested"}}], "nested"),
    ([{"type": "text"}], ""),
    ([{"type": "text", "text": "keep"}, {"type": "image_url", "image_url": {"url": "x"}}], "keep"),
    (
        [
            {"type": "text", "text": "READY", "extras": {"signature": "EusDCugDAQ=="}},
            {"type": "text", "text": "!"},
        ],
        "READY!",
    ),
])
def test_extract_text(content: Any, expected: str) -> None:
    assert extract_text(content) == expected


def test_extract_text_unknown_shape_falls_back_to_json() -> None:
    assert extract_text({"a": 1}) == '{"a": 1}'


# ------------------------------------------------------------------ adapter integration

def test_adapter_returns_plain_text_for_block_content() -> None:
    model = _BlockChatModel([
        {"type": "text", "text": "READY", "extras": {"signature": "sig"}},
    ])
    adapter = LangChainLLM(chat_model=model, provider="gemini", model="gemini-3.5-flash")  # type: ignore[arg-type]
    resp = adapter.generate("Reply with exactly one word: READY", "Reply with a single word.")
    assert resp.text == "READY"
    assert resp.is_mock is False
    assert resp.provider == "gemini" and resp.model == "gemini-3.5-flash"


def test_adapter_keeps_string_content_unchanged() -> None:
    adapter = LangChainLLM(chat_model=_BlockChatModel("plain reply"), provider="groq", model="m")  # type: ignore[arg-type]
    assert adapter.generate("hi").text == "plain reply"


# ------------------------------------------------------------------ draft_agent robustness

def test_draft_agent_parses_json_returned_as_content_blocks() -> None:
    payload = (
        '{"proposed_purpose": "Do a thing", "input_modalities": ["text"], "output_type": "text"}'
    )
    adapter = LangChainLLM(  # type: ignore[arg-type]
        chat_model=_BlockChatModel([{"type": "text", "text": payload}]),
        provider="gemini",
        model="gemini-3.5-flash",
    )
    draft = adapter.draft_agent("handle an odd request", Modality.TEXT)
    assert draft["proposed_purpose"] == "Do a thing"
    assert draft["draft_only"] is True and draft["routable"] is False
    assert draft["generator"] == "gemini:gemini-3.5-flash"


def test_draft_agent_falls_back_when_block_content_is_not_json() -> None:
    adapter = LangChainLLM(  # type: ignore[arg-type]
        chat_model=_BlockChatModel([{"type": "text", "text": "sorry, I cannot do that"}]),
        provider="gemini",
        model="gemini-3.5-flash",
    )
    draft = adapter.draft_agent("handle an odd request", Modality.TEXT)
    assert draft["routable"] is False and draft["draft_only"] is True
    assert draft["generator"] == "gemini:gemini-3.5-flash"
    assert draft["raw_suggestion"] == "sorry, I cannot do that"
