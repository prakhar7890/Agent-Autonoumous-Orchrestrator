"""Tool registry with risk tiers, code-enforced authority grants and an audit log,
plus the built-in tools.

Built-in tools are either real deterministic algorithms (text_summarize, file_analyze) or
clearly labelled STUBS (audio/image/image-generation/email). Stubs mark every output with
`stub: true` and a "[STUB ...]" text prefix. The single swap point for real models is the
`STUB TOOLS` section at the bottom of this file.
"""

from __future__ import annotations

import hashlib
import html
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from agent_repo.actors import is_human_name
from agent_repo.manifest import Modality, RiskTier
from agent_repo.privacy import scrub


class Payload(BaseModel):
    """Data flowing between steps. `path` is used for attached audio/image/file inputs."""

    model_config = ConfigDict(frozen=True)

    modality: Modality
    text: str | None = None
    path: str | None = None
    data: dict[str, Any] | None = None


class ToolError(Exception):
    """A tool failed; the runner may retry."""


class MissingInputError(Exception):
    """Required input is absent (no attachment, file not found, wrong modality). Hard stop."""


class PermissionDeniedError(PermissionError):
    """HIGH-risk tool called without a valid authority grant. Hard stop."""


class UnknownToolError(KeyError):
    pass


@dataclass(frozen=True)
class AuthorityGrant:
    tool: str
    granted_by: str
    reason: str


@dataclass(frozen=True)
class CallContext:
    actor: str
    grants: tuple[AuthorityGrant, ...] = ()


ToolFn = Callable[[Payload, dict[str, Any]], Payload]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    risk: RiskTier
    input_modalities: tuple[Modality, ...]
    output_modality: Modality
    is_stub: bool
    fn: ToolFn


@dataclass(frozen=True)
class AuditEntry:
    at: str
    tool: str
    risk: str
    actor: str
    outcome: str  # "ok" | "denied" | "error"
    detail: str = ""


@dataclass
class ToolRegistry:
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    _tools: dict[str, ToolSpec] = field(default_factory=dict)
    _audit: list[AuditEntry] = field(default_factory=list)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool {spec.name!r} already registered")
        self._tools[spec.name] = spec

    def has(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError:
            raise UnknownToolError(name) from None

    def specs(self) -> list[ToolSpec]:
        return sorted(self._tools.values(), key=lambda s: s.name)

    @property
    def audit_log(self) -> tuple[AuditEntry, ...]:
        return tuple(self._audit)

    def _log(self, spec: ToolSpec, ctx: CallContext, outcome: str, detail: str = "") -> None:
        self._audit.append(AuditEntry(self.clock().isoformat(), spec.name, spec.risk.value,
                                      scrub(ctx.actor), outcome, scrub(detail)))

    def _authorised(self, spec: ToolSpec, ctx: CallContext) -> bool:
        if spec.risk is not RiskTier.HIGH:
            return True
        return any(g.tool == spec.name and is_human_name(g.granted_by) and g.reason.strip()
                   for g in ctx.grants)

    def call(self, name: str, payload: Payload, params: dict[str, Any], ctx: CallContext) -> Payload:
        spec = self.get(name)
        if not self._authorised(spec, ctx):
            self._log(spec, ctx, "denied", "HIGH-risk tool requires an authority grant from a named human")
            raise PermissionDeniedError(f"tool {name!r} is HIGH risk and no valid authority grant was given")
        if payload.modality not in spec.input_modalities:
            self._log(spec, ctx, "error", f"input modality {payload.modality.value} not accepted")
            raise MissingInputError(
                f"tool {name!r} needs {'/'.join(m.value for m in spec.input_modalities)}, got {payload.modality.value}")
        try:
            out = spec.fn(payload, params)
        except (MissingInputError, ToolError) as exc:
            self._log(spec, ctx, "error", str(exc))
            raise
        except Exception as exc:  # noqa: BLE001 - any tool crash becomes a retryable ToolError
            self._log(spec, ctx, "error", f"{type(exc).__name__}: {exc}")
            raise ToolError(f"{name}: {type(exc).__name__}: {exc}") from exc
        self._log(spec, ctx, "ok")
        return out


# ---------------------------------------------------------------- helpers

_STOP = frozenset("a an and are as at be by for from has have in is it its of on or that the this to was were "
                  "will with we you i our they he she not but can".split())
_WORD = re.compile(r"[A-Za-z][A-Za-z']+")


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def _existing_file(payload: Payload) -> Path:
    if not payload.path:
        raise MissingInputError(f"a {payload.modality.value} attachment is required but none was given")
    path = Path(payload.path)
    if not path.is_file():
        raise MissingInputError(f"attached file not found: {path.name}")
    return path


# ---------------------------------------------------------------- REAL deterministic tools

def text_summarize(payload: Payload, params: dict[str, Any]) -> Payload:
    """Extractive summary: keep the highest-scoring sentences (word frequency), original order."""
    text = (payload.text or "").strip()
    if not text:
        raise MissingInputError("no text to summarize")
    max_sentences = int(params.get("max_sentences", 2))
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    freq = Counter(w for w in _words(text) if w not in _STOP)
    scored = sorted(range(len(sentences)),
                    key=lambda i: (-sum(freq[w] for w in _words(sentences[i]) if w not in _STOP), i))
    keep = sorted(scored[:max_sentences])
    return Payload(modality=Modality.TEXT, text=" ".join(sentences[i] for i in keep))


def file_analyze(payload: Payload, params: dict[str, Any]) -> Payload:
    """Basic statistics for a text/CSV file; binary files get size and type only."""
    path = _existing_file(payload)
    raw = path.read_bytes()
    report: dict[str, Any] = {"file_name": path.name, "extension": path.suffix.lower(), "size_bytes": len(raw)}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        report["kind"] = "binary"
    else:
        lines = text.splitlines()
        words = _words(text)
        report.update(kind="text", lines=len(lines), words=len(words),
                      top_words=[w for w, _ in Counter(w for w in words if w not in _STOP)
                                 .most_common(int(params.get("top_n", 5)))])
        if path.suffix.lower() == ".csv" and lines:
            report.update(kind="csv", columns=[c.strip() for c in lines[0].split(",")], rows=len(lines) - 1)
    summary = ", ".join(f"{k}={v}" for k, v in report.items())
    return Payload(modality=Modality.JSON, text=summary, data=report)


# ---------------------------------------------------------------- STUB TOOLS (SWAP POINT)
# Replace the bodies below with real model calls (ASR, vision classifier, image generator,
# mail gateway). Keep the signatures and the ToolSpec metadata; set is_stub=False when real.

def audio_transcribe_stub(payload: Payload, params: dict[str, Any]) -> Payload:
    """STUB ASR. Uses a sidecar `<name>.transcript.txt` if present, else a fixed placeholder."""
    path = _existing_file(payload)
    sidecar = path.with_name(path.stem + ".transcript.txt")
    body = (sidecar.read_text(encoding="utf-8").strip() if sidecar.is_file()
            else f"No speech recognition was performed on {path.name} ({path.stat().st_size} bytes).")
    return Payload(modality=Modality.TEXT, text=f"[STUB TRANSCRIPT] {body}", data={"stub": True})


_STUB_LABELS = ("document", "chart", "landscape", "person", "animal", "vehicle", "food", "building")


def image_classify_stub(payload: Payload, params: dict[str, Any]) -> Payload:
    """STUB classifier: labels derived from the file's SHA-256, not from its pixels."""
    digest = hashlib.sha256(_existing_file(payload).read_bytes()).digest()
    top_k = int(params.get("top_k", 2))
    labels = [{"label": _STUB_LABELS[digest[i] % len(_STUB_LABELS)], "score": round(digest[i + 8] / 255, 2)}
              for i in range(top_k)]
    text = "[STUB CLASSIFICATION] " + ", ".join(f"{x['label']} ({x['score']})" for x in labels)
    return Payload(modality=Modality.JSON, text=text, data={"labels": labels, "stub": True})


def image_generate_stub(payload: Payload, params: dict[str, Any]) -> Payload:
    """STUB generator: returns a placeholder SVG containing the prompt text."""
    prompt = (payload.text or "").strip()
    if not prompt:
        raise MissingInputError("no prompt for image generation")
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="512" height="128">'
           f'<rect width="512" height="128" fill="#eee"/><text x="8" y="64">STUB: {html.escape(prompt[:80])}</text></svg>')
    return Payload(modality=Modality.IMAGE, text=f"[STUB IMAGE] placeholder SVG for: {prompt[:80]}",
                   data={"format": "svg", "content": svg, "stub": True})


def email_send_stub(payload: Payload, params: dict[str, Any]) -> Payload:
    """STUB mailer (HIGH risk): never sends anything; reports what it would have sent."""
    body = (payload.text or "").strip()
    if not body:
        raise MissingInputError("no email body")
    return Payload(modality=Modality.JSON, text="[STUB EMAIL] not sent (stub)",
                   data={"sent": False, "stub": True, "chars": len(body)})


def builtin_registry(clock: Callable[[], datetime] | None = None) -> ToolRegistry:
    reg = ToolRegistry(clock=clock) if clock else ToolRegistry()
    t, a, i, f, j = Modality.TEXT, Modality.AUDIO, Modality.IMAGE, Modality.FILE, Modality.JSON
    for spec in (
        ToolSpec("text_summarize", "Extractive text summary", RiskTier.LOW, (t,), t, False, text_summarize),
        ToolSpec("file_analyze", "Statistics for text/CSV/binary files", RiskTier.LOW, (f,), j, False, file_analyze),
        ToolSpec("audio_transcribe", "STUB speech-to-text", RiskTier.LOW, (a,), t, True, audio_transcribe_stub),
        ToolSpec("image_classify", "STUB image labels", RiskTier.LOW, (i,), j, True, image_classify_stub),
        ToolSpec("image_generate", "STUB image generation (SVG placeholder)", RiskTier.MEDIUM, (t,), i, True,
                 image_generate_stub),
        ToolSpec("email_send", "STUB email sender (never sends)", RiskTier.HIGH, (t,), j, True, email_send_stub),
    ):
        reg.register(spec)
    return reg

