"""Actor naming convention.

IMPORTANT: this is a NAMING CONVENTION, NOT AUTHENTICATION. A caller who types a human-looking
name is trusted. Names starting with `llm` or `system` (case-insensitive) are treated as
non-human and may never approve, promote, deprecate, or grant authority.
"""

from __future__ import annotations

import re

_NON_HUMAN = re.compile(r"^\s*(llm|system)", re.I)


class NotHumanError(PermissionError):
    pass


def is_human_name(name: str | None) -> bool:
    return bool(name and name.strip()) and not _NON_HUMAN.match(name or "")


def require_human(name: str | None, action: str) -> str:
    if not is_human_name(name):
        raise NotHumanError(f"{action} requires a named human; {name!r} is not accepted")
    return (name or "").strip()

