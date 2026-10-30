"""Error messages must name the offending *setting/field*, never echo its value.

Audit of `agent_repo/config.py`, `agent_repo/llm.py` and `agent_repo/tools.py`: a configuration
value (which may be a secret) must not appear in an exception message. Each test injects a
distinctive value and asserts it is absent from the message while the setting name is present.

Note: propagated third-party exception text is intentionally out of scope — a provider crash is
re-raised with its own message (see `ToolRegistry.call`), which the CLI redacts via
`_safe_text`. This audit covers values read from configuration (env/`.env`) and model output
echoed by our own code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_repo.config import Settings
from agent_repo.llm import LLMError, make_llm
from agent_repo.manifest import Modality
from agent_repo.tools import Payload, ToolError, _parse_labels, text_summarize

MISSING_ENV_FILE = Path("no-such-file.env")


def test_transcriber_flag_error_names_setting_not_value() -> None:
    with pytest.raises(ValueError) as excinfo:
        Settings.from_env({"AGENT_REPO_TRANSCRIBER": "leaky-value-aaa"}, env_file=MISSING_ENV_FILE)
    message = str(excinfo.value)
    assert "AGENT_REPO_TRANSCRIBER" in message
    assert "leaky-value-aaa" not in message


def test_image_classifier_flag_error_names_setting_not_value() -> None:
    with pytest.raises(ValueError) as excinfo:
        Settings.from_env({"AGENT_REPO_IMAGE_CLASSIFIER": "leaky-value-bbb"}, env_file=MISSING_ENV_FILE)
    message = str(excinfo.value)
    assert "AGENT_REPO_IMAGE_CLASSIFIER" in message
    assert "leaky-value-bbb" not in message


def test_image_generator_flag_error_names_setting_not_value() -> None:
    with pytest.raises(ValueError) as excinfo:
        Settings.from_env({"AGENT_REPO_IMAGE_GENERATOR": "leaky-value-ccc"}, env_file=MISSING_ENV_FILE)
    message = str(excinfo.value)
    assert "AGENT_REPO_IMAGE_GENERATOR" in message
    assert "leaky-value-ccc" not in message


def test_unknown_llm_provider_hides_the_supplied_spec() -> None:
    with pytest.raises(LLMError) as excinfo:
        make_llm("leakyprovider:leakymodel", env={})
    message = str(excinfo.value)
    assert "leakyprovider" not in message
    assert "leakymodel" not in message
    assert "mock, gemini, groq" in message


def test_classifier_non_json_error_hides_model_output() -> None:
    with pytest.raises(ToolError) as excinfo:
        _parse_labels("LEAKY-MODEL-OUTPUT that is not JSON", 2)
    message = str(excinfo.value)
    assert "non-JSON" in message
    assert "LEAKY-MODEL-OUTPUT" not in message


def test_numeric_config_errors_name_setting_not_value() -> None:
    with pytest.raises(ValueError) as excinfo_float:
        Settings.from_env({"AGENT_REPO_PURPOSE_WEIGHT": "leaky-float-secret"}, env_file=MISSING_ENV_FILE)
    msg_float = str(excinfo_float.value)
    assert "AGENT_REPO_PURPOSE_WEIGHT" in msg_float
    assert "leaky-float-secret" not in msg_float

    with pytest.raises(ValueError) as excinfo_int:
        Settings.from_env({"AGENT_REPO_TOKEN_BUDGET": "leaky-int-secret"}, env_file=MISSING_ENV_FILE)
    msg_int = str(excinfo_int.value)
    assert "AGENT_REPO_TOKEN_BUDGET" in msg_int
    assert "leaky-int-secret" not in msg_int


def test_placeholder_salt_error_names_setting_not_value() -> None:
    with pytest.raises(ValueError) as excinfo:
        Settings.from_env({"AGENT_REPO_HASH_SALT": "change-me-leaky-suffix-999"}, env_file=MISSING_ENV_FILE)
    message = str(excinfo.value)
    assert "AGENT_REPO_HASH_SALT" in message
    assert "change-me-leaky-suffix-999" not in message


def test_tool_int_param_error_names_param_not_value() -> None:
    with pytest.raises(ToolError) as excinfo:
        text_summarize(Payload(modality=Modality.TEXT, text="Sentence one. Sentence two."), {"max_sentences": "leaky-param-999"})
    message = str(excinfo.value)
    assert "max_sentences" in message
    assert "leaky-param-999" not in message


