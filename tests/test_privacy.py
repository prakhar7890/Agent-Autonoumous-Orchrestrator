from __future__ import annotations

import pytest

from agent_repo.privacy import UserIdHasher, scrub, scrub_obj


@pytest.mark.parametrize("raw,placeholder", [
    ("my key is sk-abcdefghijklmnopqrstuvwx", "[REDACTED_API_KEY]"),
    ("groq gsk_ABCDEFGHIJKLMNOPQRSTUV", "[REDACTED_API_KEY]"),
    ("AIzaSyA1234567890abcdefghijklmnopqrstu", "[REDACTED_API_KEY]"),
    ("password: hunter2!", "[REDACTED_SECRET]"),
    ("api_key=abc123", "[REDACTED_SECRET]"),
    ("Authorization: Bearer abcdefghijkl", "[REDACTED_SECRET]"),
    ("mail bob.smith@example.co.in now", "[REDACTED_EMAIL]"),
    ("call +91 98765 43210", "[REDACTED_PHONE]"),
    ("call 415-555-0134", "[REDACTED_PHONE]"),
    ("card 4111 1111 1111 1111", "[REDACTED_CARD]"),
    ("aadhaar 2345 6789 0123", "[REDACTED_AADHAAR]"),
    ("PAN ABCDE1234F", "[REDACTED_PAN]"),
])
def test_scrub_patterns(raw: str, placeholder: str) -> None:
    out = scrub(raw)
    assert placeholder in out
    for secret in ("sk-abc", "hunter2", "abc123", "bob.smith", "98765", "4111", "6789", "ABCDE1234F", "0134"):
        assert secret not in out


def test_non_luhn_number_is_not_a_card() -> None:
    assert "[REDACTED_CARD]" not in scrub("order 1234 5678 9012 3456")


def test_plain_text_untouched() -> None:
    text = "Summarize the quarterly report for version 0.2 in 3 bullet points."
    assert scrub(text) == text


def test_scrub_obj_nested_and_sensitive_keys() -> None:
    out = scrub_obj({"a": ["x@y.com"], "password": "plain", "n": 3, "inner": {"api_key": "k"}})
    assert out == {"a": ["[REDACTED_EMAIL]"], "password": "[REDACTED_SECRET]", "n": 3,
                   "inner": {"api_key": "[REDACTED_SECRET]"}}


@pytest.mark.parametrize("raw", [
    "content_hash cb217e29d33bf342731eee13a234567890d72b9136",  # 12-digit run inside hex
    "id 7ba234567890123d970827",  # 12-digit run at start of hex token
    "ref 3141592653abcdef0",  # 10-digit run at start of hex token
    "order 492f9759e957414990df9",  # 9-digit run inside hex token
    "pan ABCDE1234Fxyz",  # PAN-like shape inside a longer token
])
def test_digit_rules_do_not_match_inside_alphanumeric_tokens(raw: str) -> None:
    assert scrub(raw) == raw


@pytest.mark.parametrize("raw,placeholder", [
    ("9876543210", "[REDACTED_PHONE]"),
    ("123-456-7890", "[REDACTED_PHONE]"),
    ("98765 43210", "[REDACTED_PHONE]"),
    ("4111111111111111", "[REDACTED_CARD]"),
    ("4111-1111-1111-1111", "[REDACTED_CARD]"),
    ("234567890123", "[REDACTED_AADHAAR]"),
    ("2345 6789 0123", "[REDACTED_AADHAAR]"),
    ("ABCDE1234F", "[REDACTED_PAN]"),
])
def test_standalone_digit_formats_still_redact(raw: str, placeholder: str) -> None:
    assert placeholder in scrub(raw)


@pytest.mark.parametrize("key", [
    "tokens_used", "max_tokens", "token_budget", "user_tokens", "total_token_count",
])
def test_metric_token_keys_are_not_redacted(key: str) -> None:
    assert scrub_obj({key: 7}) == {key: 7}


@pytest.mark.parametrize("key", [
    "password", "db_password", "NEO4J_PASSWORD", "secret", "client_secret",
    "api_key", "apiKey", "api_token", "access_token", "auth_token",
    "refresh_token", "authorization", "credential", "credentials", "token",
])
def test_secret_keys_are_still_redacted(key: str) -> None:
    assert scrub_obj({key: "plain-value"}) == {key: "[REDACTED_SECRET]"}


def test_hashing_is_salted_and_deterministic() -> None:
    h1, h2 = UserIdHasher("salt-one-0123456789"), UserIdHasher("salt-two-0123456789")
    assert h1.hash("bob") == h1.hash(" bob ")
    assert h1.hash("bob") != h2.hash("bob")
    assert "bob" not in h1.hash("bob")


@pytest.mark.parametrize("salt", ["", "short"])
def test_hasher_refuses_weak_salt(salt: str) -> None:
    with pytest.raises(ValueError):
        UserIdHasher(salt)


def test_hasher_refuses_empty_user() -> None:
    with pytest.raises(ValueError):
        UserIdHasher("a-long-enough-salt-value").hash("  ")

