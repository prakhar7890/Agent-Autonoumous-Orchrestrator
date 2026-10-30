"""The Quickstart "Use the library" snippet in README.md must actually run.

This test extracts the ```python block from README section 4 and executes it on the
in-memory backend (it is not parametrised over `neo4j`).
"""

from __future__ import annotations

import re
from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"


def _quickstart_library_snippet() -> str:
    text = README.read_text(encoding="utf-8")
    assert "### 4. Use the library" in text, "README Quickstart section 4 heading changed"
    section = text.split("### 4. Use the library", 1)[1]
    block = re.search(r"```python\n(.*?)```", section, re.DOTALL)
    assert block is not None, "README Quickstart section 4 has no ```python block"
    return block.group(1)


def test_readme_quickstart_library_snippet_runs_on_memory_backend(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.setenv("AGENT_REPO_HASH_SALT", "0123456789abcdef0123456789abcdef")

    snippet = _quickstart_library_snippet()
    namespace: dict[str, object] = {"__name__": "__readme_quickstart__"}
    exec(compile(snippet, str(README), "exec"), namespace)

    result = namespace["result"]
    assert result.status == "succeeded"
    assert result.output is not None
