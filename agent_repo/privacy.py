"""Privacy: regex scrubbing of secrets/PII and salted-HMAC hashing of user ids.

LIMITATION (documented in SAFETY_AND_PRIVACY.md): regex scrubbing catches well-formed
secrets and identifiers only. It does NOT catch personal names, addresses or free-form
personal data.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Callable
from typing import Any

_SENSITIVE_KEYS = re.compile(
    r"(?i)"
    r"(?:^|[_\-. ])(?:passwords?|passwd|pwd|passphrase|secrets?|apikey|api[_-]?keys?|"
    r"authorization|credentials?|bearer)(?:$|[_\-. ])"
    r"|(?:^|[_\-. ])(?:(?:api|access|auth|refresh|session|id|oauth|csrf|xsrf)[-_]?token|token)$"
)


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _card(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return "[REDACTED_CARD]" if 13 <= len(digits) <= 19 and _luhn_ok(digits) else match.group(0)


# Order matters: secrets first, then longer numeric identifiers before shorter ones.
_RULES: list[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]]] = [
    # Key/value secrets: "password: hunter2", "api_key=abc", "token = xyz"
    (re.compile(r"(?i)\b(pass(?:word)?|pwd|secret|token|api[_-]?key)\b(\s*[:=]\s*|\s+is\s+)(\S+)"),
     lambda m: f"{m.group(1)}{m.group(2)}[REDACTED_SECRET]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"), "[REDACTED_SECRET]"),
    # Well-known API key shapes (OpenAI, Groq, Google, AWS, GitHub, Slack).
    (re.compile(r"\b(sk-[A-Za-z0-9_\-]{16,}|gsk_[A-Za-z0-9]{16,}|AIza[0-9A-Za-z_\-]{30,}|AKIA[0-9A-Z]{16}"
                r"|gh[pousr]_[A-Za-z0-9]{20,}|xox[abpr]-[A-Za-z0-9\-]{10,})\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "[REDACTED_EMAIL]"),
    # Payment card: 13-19 digits with optional space/dash separators, Luhn-checked.
    # Digit-based rules require non-word boundaries on BOTH sides so they never match
    # inside alphanumeric tokens such as hex ids or content hashes.
    (re.compile(r"(?<!\w)(?:\d[ \-]?){12,18}\d(?!\w)"), _card),
    # Aadhaar: 12 digits, first digit 2-9, optional 4-4-4 grouping.
    (re.compile(r"(?<!\w)[2-9]\d{3}[ \-]?\d{4}[ \-]?\d{4}(?!\w)"), "[REDACTED_AADHAAR]"),
    # PAN: AAAAA9999A
    (re.compile(r"(?<!\w)[A-Z]{5}[0-9]{4}[A-Z](?!\w)"), "[REDACTED_PAN]"),
    # Phone: optional +country code, then 10 digits with 3-3-4 or 5-5 grouping.
    (re.compile(r"(?<![\w+])(?:\+\d{1,3}[ \-]?)?(?:(?:\(?\d{3}\)?[ \-]?)\d{3}[ \-]?|\d{5}[ \-]?)\d{4,5}(?!\w)"),
     "[REDACTED_PHONE]"),
]


def scrub(text: str) -> str:
    """Return `text` with secrets and well-formed PII replaced by placeholders."""
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text


def scrub_obj(value: Any) -> Any:
    """Recursively scrub strings in dicts/lists; values under sensitive keys are fully redacted."""
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {
            k: ("[REDACTED_SECRET]" if isinstance(k, str) and _SENSITIVE_KEYS.search(k) else scrub_obj(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub_obj(v) for v in value]
    return value


class UserIdHasher:
    """Salted HMAC-SHA256 of user ids. Raw ids are never stored."""

    def __init__(self, salt: str) -> None:
        if not salt or len(salt) < 16:
            raise ValueError("AGENT_REPO_HASH_SALT must be set to at least 16 characters")
        self._key = salt.encode("utf-8")

    def hash(self, user_id: str) -> str:
        if not user_id or not user_id.strip():
            raise ValueError("user id must be non-empty")
        digest = hmac.new(self._key, user_id.strip().encode("utf-8"), hashlib.sha256).hexdigest()
        return f"u_{digest[:32]}"

