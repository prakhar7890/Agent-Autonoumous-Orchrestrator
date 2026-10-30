"""Durable artifact storage for generated media assets (spec §2 & §6).

Binary media assets (images, audio, etc.) are stored outside graph databases to preserve
graph engine performance and prevent node bloat. Only scalar metadata (artifact_id, relative_ref,
mime, size_bytes, provider, model, owner, created_at) is stored in execution history.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agent_repo.graph.store import new_id

_ARTIFACT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_DEFAULT_MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 4 MB


class ArtifactError(Exception):
    """Base exception for artifact operations."""


class ArtifactNotFoundError(ArtifactError):
    """Requested artifact does not exist."""


class ArtifactAccessError(ArtifactError):
    """Caller does not have permission to access the artifact."""


@dataclass(frozen=True)
class ArtifactRecord:
    """Scalar metadata for a stored artifact (safe for graph node properties and API transport)."""

    artifact_id: str
    relative_ref: str
    mime: str
    size_bytes: int
    sha256: str
    provider: str
    model: str
    execution_id: str
    owner: str
    created_at: str


def detect_artifact_mime(raw: bytes) -> tuple[str, str]:
    """Detect MIME type and canonical file extension from file header magic bytes."""
    if not raw:
        raise ArtifactError("cannot store empty artifact")
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if len(raw) >= 12 and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return "image/webp", "webp"
    # Check for SVG (used by deterministic stub generators)
    stripped = raw[:1024].lstrip()
    if stripped.startswith(b"<svg") or (stripped.startswith(b"<?xml") and b"<svg" in stripped):
        return "image/svg+xml", "svg"
    raise ArtifactError("unsupported or invalid image format")


class ArtifactStore(Protocol):
    """Storage abstraction for durable media artifacts."""

    def save_image(
        self,
        raw: bytes,
        *,
        execution_id: str = "",
        provider: str = "",
        model: str = "",
        owner: str = "user",
        created_at: str = "",
    ) -> ArtifactRecord: ...

    def load_image(
        self,
        artifact_id: str,
        *,
        expected_ref: str | None = None,
        expected_owner: str | None = None,
        requester: str | None = None,
    ) -> tuple[bytes, str]: ...

    def get_metadata(self, artifact_id: str) -> ArtifactRecord | None: ...


class LocalArtifactStore:
    """Local filesystem implementation with strict path validation and size bounds."""

    def __init__(self, root_dir: Path | str, max_bytes: int = _DEFAULT_MAX_IMAGE_BYTES) -> None:
        self._root = Path(root_dir).resolve()
        self._max_bytes = max(1, int(max_bytes))
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def _validate_id(self, artifact_id: str) -> str:
        clean = (artifact_id or "").strip().lower()
        if not _ARTIFACT_ID_RE.match(clean):
            raise ArtifactError("invalid artifact id (expected 32-character hex)")
        return clean

    def _resolve_child(self, filename: str) -> Path:
        if "/" in filename or "\\" in filename or ".." in filename:
            raise ArtifactError("path traversal detected in artifact filename")
        target = (self._root / filename).resolve()
        if target.parent != self._root or not target.is_relative_to(self._root):
            raise ArtifactError("artifact path escapes storage root")
        return target

    def save_image(
        self,
        raw: bytes,
        *,
        execution_id: str = "",
        provider: str = "",
        model: str = "",
        owner: str = "user",
        created_at: str = "",
    ) -> ArtifactRecord:
        if not raw:
            raise ArtifactError("artifact payload cannot be empty")
        if len(raw) > self._max_bytes:
            raise ArtifactError(
                f"artifact size ({len(raw)} bytes) exceeds the {self._max_bytes}-byte limit"
            )
        mime, ext = detect_artifact_mime(raw)
        artifact_id = new_id()
        relative_ref = f"{artifact_id}.{ext}"
        meta_ref = f"{artifact_id}.meta.json"

        target_file = self._resolve_child(relative_ref)
        target_meta = self._resolve_child(meta_ref)

        sha = hashlib.sha256(raw).hexdigest()
        record = ArtifactRecord(
            artifact_id=artifact_id,
            relative_ref=relative_ref,
            mime=mime,
            size_bytes=len(raw),
            sha256=sha,
            provider=provider or "",
            model=model or "",
            execution_id=execution_id or "",
            owner=owner or "user",
            created_at=created_at or "",
        )

        target_file.write_bytes(raw)
        target_meta.write_text(
            json.dumps({
                "artifact_id": record.artifact_id,
                "relative_ref": record.relative_ref,
                "mime": record.mime,
                "size_bytes": record.size_bytes,
                "sha256": record.sha256,
                "provider": record.provider,
                "model": record.model,
                "execution_id": record.execution_id,
                "owner": record.owner,
                "created_at": record.created_at,
            }, indent=2),
            encoding="utf-8",
        )
        return record

    def get_metadata(self, artifact_id: str) -> ArtifactRecord | None:
        try:
            clean_id = self._validate_id(artifact_id)
        except ArtifactError:
            return None
        meta_file = self._resolve_child(f"{clean_id}.meta.json")
        if not meta_file.is_file():
            return None
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            return ArtifactRecord(**data)
        except Exception:  # noqa: BLE001
            return None

    def load_image(
        self,
        artifact_id: str,
        *,
        expected_ref: str | None = None,
        expected_owner: str | None = None,
        requester: str | None = None,
    ) -> tuple[bytes, str]:
        clean_id = self._validate_id(artifact_id)
        meta = self.get_metadata(clean_id)

        # Access control verification
        owner = expected_owner or (meta.owner if meta else "user")
        if owner and requester is not None and requester != owner:
            raise ArtifactAccessError("permission denied for artifact")

        target_file: Path | None = None
        if expected_ref:
            candidate = self._resolve_child(expected_ref)
            if candidate.is_file():
                target_file = candidate

        if target_file is None and meta is not None:
            candidate = self._resolve_child(meta.relative_ref)
            if candidate.is_file():
                target_file = candidate

        if target_file is None:
            # Fallback scan by id
            for ext in ("png", "jpg", "jpeg", "webp", "svg"):
                candidate = self._resolve_child(f"{clean_id}.{ext}")
                if candidate.is_file():
                    target_file = candidate
                    break

        if target_file is None or not target_file.is_file():
            raise ArtifactNotFoundError(f"artifact '{clean_id}' not found")

        raw = target_file.read_bytes()
        if len(raw) > self._max_bytes:
            raise ArtifactError("stored artifact exceeds configured maximum bytes")
        mime, _ = detect_artifact_mime(raw)
        return raw, mime


def get_default_artifact_store(settings: Any | None = None) -> LocalArtifactStore:
    """Build a LocalArtifactStore configured from settings or environment."""
    import tempfile
    from agent_repo.config import Settings

    s = settings if isinstance(settings, Settings) else Settings.from_env()
    root = s.tools.artifact_dir or (Path(tempfile.gettempdir()) / "agent_repo_artifacts")
    return LocalArtifactStore(root, max_bytes=s.tools.max_image_bytes)
