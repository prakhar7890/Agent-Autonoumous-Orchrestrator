"""Unit tests for the durable artifact storage system (spec §2 & §6)."""

from __future__ import annotations

from pathlib import Path
import pytest

from agent_repo.artifacts import (
    ArtifactAccessError,
    ArtifactError,
    ArtifactNotFoundError,
    LocalArtifactStore,
    detect_artifact_mime,
)

# Minimal valid magic-byte signatures
TINY_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
TINY_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00C\x00\xff\xd9"
TINY_WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8L\x0e\x00\x00\x00/\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
TINY_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect fill="blue"/></svg>'


def test_detect_artifact_mime() -> None:
    assert detect_artifact_mime(TINY_PNG) == ("image/png", "png")
    assert detect_artifact_mime(TINY_JPEG) == ("image/jpeg", "jpg")
    assert detect_artifact_mime(TINY_WEBP) == ("image/webp", "webp")
    assert detect_artifact_mime(TINY_SVG) == ("image/svg+xml", "svg")

    with pytest.raises(ArtifactError, match="empty"):
        detect_artifact_mime(b"")

    with pytest.raises(ArtifactError, match="unsupported or invalid"):
        detect_artifact_mime(b"NOT_AN_IMAGE_HEADER_AT_ALL")


def test_local_artifact_store_crud(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts", max_bytes=1024 * 1024)

    record = store.save_image(
        TINY_PNG,
        execution_id="exec-123",
        provider="test-provider",
        model="test-model",
        owner="user",
        created_at="2026-10-22T00:00:00Z",
    )
    assert len(record.artifact_id) == 32
    assert record.mime == "image/png"
    assert record.size_bytes == len(TINY_PNG)
    assert record.provider == "test-provider"
    assert record.model == "test-model"

    # Load back
    loaded_bytes, loaded_mime = store.load_image(record.artifact_id)
    assert loaded_bytes == TINY_PNG
    assert loaded_mime == "image/png"

    # Get metadata
    meta = store.get_metadata(record.artifact_id)
    assert meta is not None
    assert meta.artifact_id == record.artifact_id
    assert meta.sha256 == record.sha256


def test_local_artifact_store_path_traversal_and_invalid_id(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")

    with pytest.raises(ArtifactError, match="invalid artifact id"):
        store.load_image("../../etc/passwd")

    with pytest.raises(ArtifactError, match="invalid artifact id"):
        store.load_image("not-a-valid-hex-id-too-short")

    valid_id = "0123456789abcdef0123456789abcdef"
    with pytest.raises(ArtifactError, match="path traversal"):
        store.load_image(valid_id, expected_ref="../escaped.png")


def test_local_artifact_store_size_and_empty_guards(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts", max_bytes=100)

    with pytest.raises(ArtifactError, match="empty"):
        store.save_image(b"")

    with pytest.raises(ArtifactError, match="exceeds"):
        store.save_image(b"x" * 200)


def test_local_artifact_store_access_control(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    record = store.save_image(TINY_JPEG, owner="alice")

    # Mismatched requester -> ArtifactAccessError
    with pytest.raises(ArtifactAccessError, match="permission denied"):
        store.load_image(record.artifact_id, requester="bob")

    # Matching requester -> succeeds
    raw, mime = store.load_image(record.artifact_id, requester="alice")
    assert raw == TINY_JPEG
    assert mime == "image/jpeg"

    # Non-existent artifact -> ArtifactNotFoundError
    with pytest.raises(ArtifactNotFoundError):
        store.load_image("0123456789abcdef0123456789abcdef")

