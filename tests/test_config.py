from __future__ import annotations

from pathlib import Path

from agent_repo.config import Settings


def test_env_overrides_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("AGENT_REPO_HASH_SALT=from-file\nNEO4J_URI=bolt://file:7687\n", encoding="utf-8")
    s = Settings.from_env({"AGENT_REPO_HASH_SALT": "from-env"}, env_file=env_file)
    assert s.hash_salt == "from-env"
    assert s.neo4j is not None and s.neo4j.uri == "bolt://file:7687" and s.neo4j.user == "neo4j"


def test_no_neo4j_without_uri(tmp_path: Path) -> None:
    assert Settings.from_env({}, env_file=tmp_path / "missing").neo4j is None


def test_settings_reject_change_me_placeholder_salt(tmp_path: Path) -> None:
    import pytest

    missing = tmp_path / "missing"
    for placeholder in ("change-me-to-a-long-random-string", "CHANGE-ME", "change-me"):
        with pytest.raises(ValueError):
            Settings.from_env({"AGENT_REPO_HASH_SALT": placeholder}, env_file=missing)
    with pytest.raises(ValueError):
        Settings(hash_salt="change-me-to-a-long-random-string")
    ok = Settings.from_env({"AGENT_REPO_HASH_SALT": "9f8e7d6c5b4a39281706f5e4d3c2b1a0"}, env_file=missing)
    assert ok.hash_salt == "9f8e7d6c5b4a39281706f5e4d3c2b1a0"


def test_validate_neo4j_uri_rejects_missing_and_placeholders() -> None:
    import pytest
    from agent_repo.graph.neo4j_store import Neo4jConnectionError, validate_neo4j_uri

    for bad in ("", "   ", "neo4j+s://<id>.databases.neo4j.io", "neo4j+s://xxxx.databases.neo4j.io", "not-a-uri"):
        with pytest.raises(Neo4jConnectionError):
            validate_neo4j_uri(bad)
    assert validate_neo4j_uri("neo4j+s://abcd1234.databases.neo4j.io") == "abcd1234.databases.neo4j.io"


