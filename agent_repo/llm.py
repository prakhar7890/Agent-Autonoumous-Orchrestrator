"""LLM provider interface, deterministic offline MockLLM, LangChain chat-model adapter,
and `make_llm("provider:model")` factory (spec §2, §6, §9 M3).

All scripted/offline responses from `MockLLM` carry `is_mock=True` and a clear `MOCK LLM`
label so a deterministic offline script is never mistaken for a live model call.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from dotenv import dotenv_values
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from agent_repo.manifest import Modality
from agent_repo.privacy import scrub


class LLMError(Exception):
    """Configuration or invocation error for an LLM provider."""


@dataclass(frozen=True)
class LLMResponse:
    text: str
    provider: str
    model: str
    is_mock: bool = False
    tokens_used: int = 0


class LLMProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    @property
    def is_mock(self) -> bool: ...

    def generate(self, prompt: str, system_prompt: str = "") -> LLMResponse: ...

    def draft_agent(self, query: str, payload_modality: Modality) -> dict[str, Any]: ...


@dataclass
class MockLLM:
    """Deterministic offline LLM provider (`MOCK LLM`) with scriptable responses for tests and demo."""

    model: str = "mock-1"
    scripted_responses: list[str] = field(default_factory=list)
    router_fn: Callable[[str, str], str] | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def model_name(self) -> str:
        return self.model

    @property
    def is_mock(self) -> bool:
        return True

    def queue_response(self, response_text: str) -> None:
        self.scripted_responses.append(response_text)

    def generate(self, prompt: str, system_prompt: str = "") -> LLMResponse:
        self.calls.append((system_prompt, prompt))
        if self.scripted_responses:
            text = self.scripted_responses.pop(0)
        elif self.router_fn is not None:
            text = self.router_fn(system_prompt, prompt)
        else:
            clean = scrub(prompt).strip()
            text = json.dumps({
                "label": "MOCK LLM",
                "system_prompt": "You summarize text faithfully, concisely, and clearly. Never invent facts.",
                "pipeline": [{"kind": "tool", "name": "text_summarize", "params": {"max_sentences": 2}}],
                "note": f"MOCK LLM deterministic response for: {clean[:80]}",
            })
        tokens = max(10, (len(prompt) + len(system_prompt) + len(text)) // 4)
        return LLMResponse(
            text=text,
            provider="mock",
            model=self.model,
            is_mock=True,
            tokens_used=tokens,
        )

    def draft_agent(self, query: str, payload_modality: Modality) -> dict[str, Any]:
        clean = scrub(query)
        return {
            "generator": "MOCK LLM",
            "draft_only": True,
            "routable": False,
            "proposed_purpose": f"Handle request: {clean[:120]}",
            "input_modalities": [payload_modality.value],
            "output_type": Modality.TEXT.value,
            "note": "Drafted by MOCK LLM; stored as data only and never routable.",
        }


class LangChainLLM:
    """Adapter wrapping any LangChain `BaseChatModel` behind `LLMProvider`."""

    def __init__(self, chat_model: BaseChatModel, provider: str, model: str) -> None:
        self._chat = chat_model
        self._provider = provider
        self._model = model

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def is_mock(self) -> bool:
        return False

    def generate(self, prompt: str, system_prompt: str = "") -> LLMResponse:
        messages: list[BaseMessage] = []
        if system_prompt.strip():
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=prompt))
        ai_msg: AIMessage = self._chat.invoke(messages)  # type: ignore[assignment]
        content = ai_msg.content if isinstance(ai_msg.content, str) else json.dumps(ai_msg.content)
        tokens = max(10, (len(prompt) + len(system_prompt) + len(content)) // 4)
        return LLMResponse(
            text=content,
            provider=self._provider,
            model=self._model,
            is_mock=False,
            tokens_used=tokens,
        )

    def draft_agent(self, query: str, payload_modality: Modality) -> dict[str, Any]:
        clean = scrub(query)
        resp = self.generate(
            prompt=(
                f"Propose a JSON draft agent specification (fields: proposed_purpose, input_modalities, output_type) "
                f"for unmatched query: {clean!r} with payload modality {payload_modality.value!r}."
            ),
            system_prompt="Return JSON only. This draft will be stored as non-routable data.",
        )
        try:
            parsed = json.loads(resp.text)
            if isinstance(parsed, dict):
                return {**parsed, "draft_only": True, "routable": False, "generator": f"{self._provider}:{self._model}"}
        except json.JSONDecodeError:
            pass
        return {
            "generator": f"{self._provider}:{self._model}",
            "draft_only": True,
            "routable": False,
            "proposed_purpose": f"Handle request: {clean[:120]}",
            "raw_suggestion": scrub(resp.text[:240]),
            "input_modalities": [payload_modality.value],
            "output_type": Modality.TEXT.value,
        }


def _merged_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    file_vals = {
        k: v for k, v in dotenv_values(Path(".env")).items() if v is not None
    } if Path(".env").is_file() else {}
    return {**file_vals, **(env if env is not None else os.environ)}


def make_llm(
    spec: str = "mock:mock-1",
    env: Mapping[str, str] | None = None,
    chat_model: BaseChatModel | None = None,
) -> LLMProvider:
    """Factory creating an `LLMProvider` from `'provider:model'` (e.g. `'mock:mock-1'`,
    `'gemini:gemini-1.5-flash'`, `'groq:llama-3.1-8b-instant'`, `'openai:gpt-4o-mini'`).

    If `chat_model` is passed explicitly, wraps it in `LangChainLLM` directly (used in unit tests
    and custom LangChain pipelines).
    """
    if ":" in spec:
        provider, model = spec.split(":", 1)
    else:
        provider, model = spec, "default"
    provider = provider.strip().lower()
    model = model.strip() or "default"

    if chat_model is not None:
        return LangChainLLM(chat_model=chat_model, provider=provider, model=model)

    if provider == "mock":
        return MockLLM(model=model)

    values = _merged_env(env)

    if provider in ("gemini", "google"):
        api_key = values.get("GOOGLE_API_KEY", "").strip()
        if not api_key:
            raise LLMError("GOOGLE_API_KEY is not set in .env or environment for provider 'gemini'")
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError as exc:
            raise LLMError("Install 'langchain-google-genai' to use the gemini provider") from exc
        return LangChainLLM(ChatGoogleGenerativeAI(model=model, google_api_key=api_key), "gemini", model)

    if provider == "groq":
        api_key = values.get("GROQ_API_KEY", "").strip()
        if not api_key:
            raise LLMError("GROQ_API_KEY is not set in .env or environment for provider 'groq'")
        try:
            from langchain_groq import ChatGroq
        except ImportError as exc:
            raise LLMError("Install 'langchain-groq' to use the groq provider") from exc
        return LangChainLLM(ChatGroq(model=model, api_key=api_key), "groq", model)  # type: ignore[arg-type]

    if provider == "openai":
        api_key = values.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise LLMError("OPENAI_API_KEY is not set in .env or environment for provider 'openai'")
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise LLMError("Install 'langchain-openai' to use the openai provider") from exc
        return LangChainLLM(ChatOpenAI(model=model, api_key=api_key), "openai", model)  # type: ignore[arg-type]

    raise LLMError(f"Unknown LLM provider {provider!r} in spec {spec!r}; expected mock, gemini, groq, or openai")

