"""Read-mostly local FastAPI service over the agent repository.

Importing this package must not require FastAPI/uvicorn. Use ``from agent_repo.api.app import
create_app`` (which needs the ``ui`` extra) to build the app, or ``python -m agent_repo.api`` to
run it.
"""

from __future__ import annotations

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    from agent_repo.api.__main__ import main as _main

    return _main(argv)
