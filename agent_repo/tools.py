"""Tool registry with risk tiers, code-enforced authority grants and an audit log,
plus the built-in tools.

Built-in tools are either real deterministic algorithms (text_summarize, file_analyze) or
clearly labelled STUBS. Stubs mark every output with `stub: true` and a "[STUB ...]" text
prefix. The single swap point for real models is the `TOOL IMPLEMENTATIONS (SWAP POINT)` section
at the bottom of this file.

`audio_transcribe` and `image_classify` now have **opt-in** real implementations (Groq ASR,
Gemini vision) selected per tool through `ToolSettings`; the stubs stay the default so the offline
suite and demo remain deterministic. `image_generate` and `email_send` have no real implementation
yet and always run the stub. A real tool's output is never labelled `stub`.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import mimetypes
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from agent_repo.actors import is_human_name
from agent_repo.config import (
    ToolSettings,
    _DEFAULT_GEMINI_IMAGE_MODEL,
    _DEFAULT_HF_IMAGE_MODEL,
    _DEFAULT_MAX_IMAGE_BYTES,
    merged_env,
)
from agent_repo.manifest import Modality, RiskTier
from agent_repo.privacy import scrub

# Image magic bytes → MIME type. Only these three are accepted as real generated images.
_IMAGE_MAGIC = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
}


def _image_mime_from_magic(raw: bytes) -> str:
    """Return the MIME type for `raw` based on its magic bytes, or raise if not a real image."""
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    raise ToolError("provider returned no image")


class Payload(BaseModel):
    """Data flowing between steps. `path` is used for attached audio/image/file inputs."""

    model_config = ConfigDict(frozen=True)

    modality: Modality
    text: str | None = None
    path: str | None = None
    data: dict[str, Any] | None = None


class ToolError(Exception):
    """A tool failed; the runner may retry when `retryable` is True."""

    def __init__(self, message: str = "", *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = bool(retryable)


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


def _int_param(params: dict[str, Any], name: str, default: int) -> int:
    try:
        return int(params.get(name, default))
    except (TypeError, ValueError):
        raise ToolError(f"parameter {name!r} must be an integer") from None


def _existing_file(payload: Payload) -> Path:
    if not payload.path:
        raise MissingInputError(f"a {payload.modality.value} attachment is required but none was given")
    path = Path(payload.path)
    if not path.is_file():
        raise MissingInputError(f"attached file not found: {path.name}")
    return path


# ---------------------------------------------------------------- REAL deterministic tools

_INSTRUCTION_PREFIX_RE = re.compile(
    r"^(?:summarize(?:\s+(?:the\s+key\s+points|this|that|it))?)\s*[:]\s*",
    re.IGNORECASE,
)


def _strip_instruction_prefix(text: str) -> str | None:
    """Remove a leading summarization instruction prefix only when clearly separable.

    Strips patterns like ``summarize: <content>`` or ``summarize the key points: <content>``.
    Returns ``None`` when no separable prefix is found (caller should summarize the whole input).
    Returns the empty string when a prefix is found but no content follows it.
    Does not touch text where the colon appears mid-sentence (e.g. ``Beta launch: 2024``),
    because that would discard legitimate content.
    """
    m = _INSTRUCTION_PREFIX_RE.match(text)
    if not m:
        return None
    return text[m.end():].strip()


def _extract_docx_text(raw: bytes) -> str | None:
    """Extract plain text paragraphs from a `.docx` (Office Open XML ZIP) byte stream."""
    import io
    import xml.etree.ElementTree as ET
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            if "word/document.xml" not in zf.namelist():
                return None
            xml_bytes = zf.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
    except Exception:  # noqa: BLE001
        return None

    paragraphs: list[str] = []
    for elem in root.iter():
        if elem.tag.endswith("}p") or elem.tag == "p":
            runs = [
                t.text
                for t in elem.iter()
                if (t.tag.endswith("}t") or t.tag == "t") and t.text
            ]
            para = "".join(runs).strip()
            if para:
                if para[-1] not in ".!?":
                    para = para + "."
                paragraphs.append(para)
    return "\n".join(paragraphs) if paragraphs else None


def _extract_pdf_text(raw: bytes) -> str | None:
    """Extract embedded PDF text; return None for corrupt, encrypted, or scanned PDFs."""
    try:
        from io import BytesIO
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(raw), strict=False)
        if reader.is_encrypted:
            return None
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
        text = "\n\f\n".join(pages).strip()
        return text or None
    except Exception:  # noqa: BLE001 - unsupported/corrupt PDFs become explicit binary results
        return None


def _extractive_summary(content: str, max_sentences: int = 4) -> str:
    """Score sentences by non-stopword frequency and return the top `max_sentences` in original order."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", content) if s.strip()]
    if not sentences:
        return ""
    freq = Counter(w for w in _words(content) if w not in _STOP)
    scored = sorted(
        range(len(sentences)),
        key=lambda i: (-sum(freq[w] for w in _words(sentences[i]) if w not in _STOP), i),
    )
    keep = sorted(scored[: min(max_sentences, len(sentences))])
    return " ".join(sentences[i] for i in keep)


def text_summarize(payload: Payload, params: dict[str, Any]) -> Payload:
    """Extractive summary: keep the highest-scoring sentences (word frequency), original order."""
    text = (payload.text or "").strip()
    if not text and payload.path:
        p = Path(payload.path)
        if p.is_file():
            raw = p.read_bytes()
            text = (_extract_docx_text(raw) if p.suffix.lower() == ".docx" else None) or raw.decode("utf-8", errors="ignore").strip()
    if not text:
        raise MissingInputError("no text to summarize")

    # Separate a leading instruction prefix from the content to summarize.
    content = _strip_instruction_prefix(text)
    if content is None:
        content = text  # no separable instruction prefix; summarize the whole input
    if not content:
        raise MissingInputError("no text to summarize after instruction prefix")

    max_sentences = _int_param(params, "max_sentences", 4)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", content) if s.strip()]
    if not sentences:
        raise ToolError("summarization produced no sentences")
    freq = Counter(w for w in _words(content) if w not in _STOP)
    scored = sorted(range(len(sentences)),
                    key=lambda i: (-sum(freq[w] for w in _words(sentences[i]) if w not in _STOP), i))
    keep = sorted(scored[:min(max_sentences, len(sentences))])
    summary = " ".join(sentences[i] for i in keep)
    if not summary:
        raise ToolError("summarization produced an empty result")
    return Payload(modality=Modality.TEXT, text=summary)


def file_analyze(payload: Payload, params: dict[str, Any]) -> Payload:
    """Basic statistics for text/CSV/DOCX/PDF files; scanned PDFs remain unsupported."""
    path = _existing_file(payload)
    raw = path.read_bytes()
    ext = path.suffix.lower()
    report: dict[str, Any] = {"file_name": path.name, "extension": ext, "size_bytes": len(raw)}
    docx_text = _extract_docx_text(raw) if ext == ".docx" else None
    pdf_text = _extract_pdf_text(raw) if ext == ".pdf" else None
    if docx_text is not None:
        lines = [ln for ln in docx_text.splitlines() if ln.strip()]
        words = _words(docx_text)
        report.update(
            kind="docx",
            lines=len(lines),
            words=len(words),
            top_words=[
                w for w, _ in Counter(w for w in words if w not in _STOP)
                .most_common(_int_param(params, "top_n", 5))
            ],
            summary=_extractive_summary(docx_text, max_sentences=_int_param(params, "max_sentences", 5)),
        )
    elif pdf_text is not None:
        lines = [ln for ln in pdf_text.splitlines() if ln.strip()]
        words = _words(pdf_text)
        report.update(
            kind="pdf",
            pages=pdf_text.count("\f") + 1,
            lines=len(lines),
            words=len(words),
            summary=_extractive_summary(pdf_text, max_sentences=_int_param(params, "max_sentences", 5)),
            provenance="embedded PDF text; OCR is not implemented",
        )
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            report["kind"] = "binary"
        else:
            lines = text.splitlines()
            words = _words(text)
            report.update(kind="text", lines=len(lines), words=len(words),
                          top_words=[w for w, _ in Counter(w for w in words if w not in _STOP)
                                     .most_common(_int_param(params, "top_n", 5))])
            if ext == ".csv" and lines:
                report.update(kind="csv", columns=[c.strip() for c in lines[0].split(",")], rows=len(lines) - 1)
            elif text.strip():
                report["summary"] = _extractive_summary(text, max_sentences=_int_param(params, "max_sentences", 4))
    summary = ", ".join(f"{k}={v}" for k, v in report.items())
    return Payload(modality=Modality.JSON, text=summary, data=report)


# ------------------------------------------------------ TOOL IMPLEMENTATIONS (SWAP POINT)
# Stubs and real providers live side by side here. Keep the signatures and the ToolSpec metadata;
# select a real provider through ToolSettings (`builtin_registry(tool_settings=...)`). Real
# implementations must not label their output `stub`.

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
    top_k = _int_param(params, "top_k", 2)
    labels = [{"label": _STUB_LABELS[digest[i] % len(_STUB_LABELS)], "score": round(digest[i + 8] / 255, 2)}
              for i in range(top_k)]
    text = "[STUB CLASSIFICATION] " + ", ".join(f"{x['label']} ({x['score']})" for x in labels)
    return Payload(modality=Modality.JSON, text=text, data={"labels": labels, "stub": True})


def image_generate_stub(payload: Payload, params: dict[str, Any]) -> Payload:
    """STUB generator: returns a tiny placeholder SVG (grey card) containing the escaped prompt."""
    prompt = (payload.text or "").strip()
    if not prompt:
        raise MissingInputError("no prompt for image generation")
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="512" height="128">'
           '<rect width="512" height="128" fill="#eeeeee"/>'
           f'<text x="8" y="64">STUB IMAGE: {html.escape(prompt[:80])}</text></svg>')
    return Payload(modality=Modality.IMAGE, text=f"[STUB IMAGE] placeholder SVG for: {prompt[:80]}",
                   data={"format": "svg", "content": svg, "stub": True})


def email_send_stub(payload: Payload, params: dict[str, Any]) -> Payload:
    """STUB mailer (HIGH risk): never sends anything; reports what it would have sent."""
    body = (payload.text or "").strip()
    if not body:
        raise MissingInputError("no email body")
    return Payload(modality=Modality.JSON, text="[STUB EMAIL] not sent (stub)",
                   data={"sent": False, "stub": True, "chars": len(body)})


# ---------------------------------------------------------------- REAL PROVIDER TOOLS
# Client builders are module-level so tests can substitute a fake client (no network needed).

_GROQ_ASR_MODEL = "whisper-large-v3"
_GEMINI_VISION_MODEL = "gemini-3.5-flash"
_CLASSIFY_INSTRUCTION = (
    "Classify the image into at most {top_k} labels. Reply with JSON only, no prose: "
    '{{"labels":[{{"label":"<short noun>","score":<number between 0 and 1>}}]}}'
    " ordered most likely first."
)


def _groq_transcription_client(api_key: str) -> Any:
    try:
        from groq import Groq
    except ImportError:
        raise ToolError(
            'groq is not installed; install the "providers" extra (`pip install -e ".[providers]"`) '
            "to use AGENT_REPO_TRANSCRIBER=groq"
        ) from None
    return Groq(api_key=api_key)


def _gemini_client(api_key: str) -> Any:
    try:
        from google import genai
    except ImportError:
        raise ToolError(
            'google-genai is not installed; install the "providers" extra (`pip install -e ".[providers]"`) '
            "to use AGENT_REPO_IMAGE_CLASSIFIER=gemini"
        ) from None
    return genai.Client(api_key=api_key)


def _gemini_images_client(api_key: str) -> Any:
    try:
        from google import genai
    except ImportError:
        raise ToolError(
            'google-genai is not installed; install the "providers" extra (`pip install -e ".[providers]"`) '
            "to use AGENT_REPO_IMAGE_GENERATOR=gemini"
        ) from None
    return genai.Client(api_key=api_key)


def audio_transcribe_groq(payload: Payload, params: dict[str, Any]) -> Payload:
    """Real speech-to-text via Groq's OpenAI-compatible transcription API (default whisper-large-v3).

    Needs `GROQ_API_KEY` in `.env`/environment. Provider failures raise `ToolError` (retryable).
    """
    path = _existing_file(payload)
    api_key = merged_env().get("GROQ_API_KEY", "").strip()
    if not api_key:
        raise ToolError("GROQ_API_KEY is not set in .env or environment; cannot transcribe with groq")
    model = str(params.get("model") or _GROQ_ASR_MODEL)
    client = _groq_transcription_client(api_key)
    try:
        with path.open("rb") as handle:
            response = client.audio.transcriptions.create(model=model, file=(path.name, handle.read()))
    except Exception as exc:  # noqa: BLE001 - any provider failure is a retryable ToolError
        raise ToolError(f"groq transcription failed: {type(exc).__name__}: {exc}") from exc
    text = (getattr(response, "text", "") or "").strip() or "(no speech detected)"
    return Payload(modality=Modality.TEXT, text=text,
                   data={"provider": "groq", "model": model, "stub": False})


def _parse_labels(text: str, top_k: int) -> list[dict[str, Any]]:
    """Parse a vision model's JSON reply into `[{"label": str, "score": float}, ...]`."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ToolError("classifier returned non-JSON output") from exc
    raw = parsed.get("labels") if isinstance(parsed, dict) else parsed
    if not isinstance(raw, list):
        raise ToolError("classifier JSON has no 'labels' list")
    labels: list[dict[str, Any]] = []
    for item in raw[:top_k]:
        if not isinstance(item, dict) or "label" not in item:
            continue
        try:
            score = round(float(item.get("score", 0.0)), 2)
        except (TypeError, ValueError):
            score = 0.0
        labels.append({"label": str(item["label"]), "score": score})
    if not labels:
        raise ToolError("classifier produced no usable labels")
    return labels


def _gemini_image_part(data: bytes, mime_type: str) -> Any:
    try:
        from google.genai import types
    except ImportError:
        return {"data": data, "mime_type": mime_type}
    return types.Part.from_bytes(data=data, mime_type=mime_type)


def image_classify_gemini(payload: Payload, params: dict[str, Any]) -> Payload:
    """Real image labelling via a Gemini vision model. Needs `GOOGLE_API_KEY`; failures raise `ToolError`."""
    path = _existing_file(payload)
    api_key = merged_env().get("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise ToolError("GOOGLE_API_KEY is not set in .env or environment; cannot classify with gemini")
    top_k = _int_param(params, "top_k", 2)
    model = str(params.get("model") or _GEMINI_VISION_MODEL)
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    client = _gemini_client(api_key)
    image_part = _gemini_image_part(path.read_bytes(), mime)
    try:
        response = client.models.generate_content(
            model=model, contents=[_CLASSIFY_INSTRUCTION.format(top_k=top_k), image_part])
    except Exception as exc:  # noqa: BLE001 - any provider failure is a retryable ToolError
        raise ToolError(f"gemini classification failed: {type(exc).__name__}: {exc}") from exc
    labels = _parse_labels(getattr(response, "text", "") or "", top_k)
    text = " ".join(f"{x['label']} ({x['score']})" for x in labels)
    return Payload(modality=Modality.JSON, text=text,
                   data={"labels": labels, "provider": "gemini", "model": model, "stub": False})


_NON_RETRYABLE_STATUS_CODES = frozenset({400, 401, 402, 403, 404, 410, 429})
_FILE_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/][^\s'\"<>|]+|(?<![A-Za-z0-9])/(?:tmp|var|home|Users|etc|opt|usr|mnt|private|run)[/\\][^\s'\"<>|]+)"
)


def _is_retryable_provider_error(exc: Exception) -> bool:
    """Return False for permanent HTTP status codes (400, 401, 402, 403, 404, 410, 429); True otherwise."""
    for attr in ("code", "status_code", "http_status"):
        val = getattr(exc, attr, None)
        if isinstance(val, int) and val in _NON_RETRYABLE_STATUS_CODES:
            return False
        if isinstance(val, str) and val.strip().isdigit() and int(val.strip()) in _NON_RETRYABLE_STATUS_CODES:
            return False
    return re.search(r"(?<!\d)(400|401|402|403|404|410|429)(?!\d)", str(exc)) is None


def _sanitize_provider_error(
    exc: Exception | str,
    prompt: str = "",
    *,
    prefix: str = "gemini image generation failed: ",
) -> str:
    """Return a clean, secret-free provider error message (no traceback, no leaked details, max 200 chars)."""
    text = str(exc)
    if prompt and prompt.strip():
        text = text.replace(prompt.strip(), "[REDACTED_PROMPT]")
        if ":" in prompt:
            tail = prompt.split(":", 1)[1].strip()
            if len(tail) >= 4:
                text = text.replace(tail, "[REDACTED_PROMPT]")
    for marker in ("Details:", "Please refer to", "Learn more", "See https", "Error details:", "Traceback (most recent call last)"):
        idx = text.find(marker)
        if idx >= 0:
            text = text[:idx]
    text = _FILE_PATH_RE.sub("[REDACTED_PATH]", text)
    text = scrub(text).strip()
    if not text:
        text = type(exc).__name__ if isinstance(exc, Exception) else "ToolError"
    out = f"{prefix}{text}" if prefix and not text.startswith(prefix.strip()) else text
    return out[:200]


def image_generate_gemini(payload: Payload, params: dict[str, Any]) -> Payload:
    """Real image generation via Gemini. Needs `GOOGLE_API_KEY`; failures raise `ToolError`.

    The model id comes from `AGENT_REPO_GEMINI_IMAGE_MODEL` (verified: `gemini-nano-banana-2.1`);
    the size cap comes from `AGENT_REPO_API_MAX_IMAGE_BYTES` (default 4 MB).
    The prompt text never appears in any error message.
    """
    prompt = (payload.text or "").strip()
    if not prompt:
        raise MissingInputError("no prompt for image generation")
    api_key = merged_env().get("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise ToolError("GOOGLE_API_KEY is not set in .env or environment; cannot generate images with gemini")
    values = merged_env()
    model = str(params.get("model") or values.get("AGENT_REPO_GEMINI_IMAGE_MODEL", _DEFAULT_GEMINI_IMAGE_MODEL)).strip() or _DEFAULT_GEMINI_IMAGE_MODEL
    try:
        max_bytes = int(str(values.get("AGENT_REPO_API_MAX_IMAGE_BYTES", str(_DEFAULT_MAX_IMAGE_BYTES))))
    except (TypeError, ValueError):
        max_bytes = _DEFAULT_MAX_IMAGE_BYTES
    client = _gemini_images_client(api_key)
    try:
        from google.genai import types
    except ImportError as exc:  # noqa: BLE001
        raise ToolError(
            'google-genai is not installed; install the "providers" extra '
            '(`pip install -e ".[providers]"`) to use AGENT_REPO_IMAGE_GENERATOR=gemini'
        ) from exc
    # Google doc: https://ai.google.dev/gemini-api/docs/image-generation  (enable image output via
    # response_modalities in GenerateContentConfig; read bytes+mime from response parts' inline_data)
    try:
        config = types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"])
        response = client.models.generate_content(model=model, contents=prompt, config=config)
    except Exception as exc:  # noqa: BLE001 - provider failure -> clean ToolError
        raise ToolError(
            _sanitize_provider_error(exc, prompt=prompt),
            retryable=_is_retryable_provider_error(exc),
        ) from exc
    image_bytes = None
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        if content is None:
            continue
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline is None:
                continue
            data = getattr(inline, "data", None)
            part_mime = getattr(inline, "mime_type", None) or ""
            if data and part_mime in ("image/png", "image/jpeg", "image/webp"):
                image_bytes = data
                break
        if image_bytes is not None:
            break
    if not image_bytes:
        raise ToolError("provider returned no image")
    mime = _image_mime_from_magic(image_bytes)  # validates magic bytes; raises if not a real image
    if len(image_bytes) > max_bytes:
        raise ToolError("generated image exceeds size cap")
    return Payload(modality=Modality.IMAGE, text=f"[{mime}, {len(image_bytes)} bytes, generated by {model}]",
                   data={"format": mime.split("/")[-1], "content": image_bytes, "stub": False,
                         "provider": "gemini", "model": model, "mime": mime})


def _hf_space_flux_schnell(prompt: str, api_key: str = "", max_bytes: int = _DEFAULT_MAX_IMAGE_BYTES) -> bytes:
    """Generate an image via the public `black-forest-labs/FLUX.1-schnell` Hugging Face Space (ZeroGPU)."""
    import urllib.request

    base = "https://black-forest-labs-flux-1-schnell.hf.space"
    for token in ([api_key, ""] if api_key else [""]):
        try:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            sse_headers: dict[str, str] = {}
            if token:
                headers["Authorization"] = f"Bearer {token}"
                sse_headers["Authorization"] = f"Bearer {token}"
            call_req = urllib.request.Request(
                f"{base}/gradio_api/call/infer",
                data=json.dumps({"data": [prompt, 0, True, 512, 512, 4]}).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(call_req, timeout=15) as resp:
                event_id = json.loads(resp.read().decode("utf-8"))["event_id"]

            sse_req = urllib.request.Request(f"{base}/gradio_api/call/infer/{event_id}", headers=sse_headers)
            with urllib.request.urlopen(sse_req, timeout=30) as sse_resp:
                sse_text = sse_resp.read().decode("utf-8", errors="replace")

            data_payload = None
            last_event = ""
            for line in sse_text.splitlines():
                if line.startswith("event:"):
                    last_event = line.split(":", 1)[1].strip()
                elif line.startswith("data:") and last_event == "complete":
                    data_payload = json.loads(line.split(":", 1)[1].strip())
                    break
            if not data_payload or not isinstance(data_payload, list) or not isinstance(data_payload[0], dict):
                continue
            image_url = data_payload[0].get("url")
            if not image_url:
                continue
            img_req = urllib.request.Request(str(image_url), headers=sse_headers)
            with urllib.request.urlopen(img_req, timeout=20) as img_resp:
                raw = img_resp.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    continue
                if raw:
                    return raw
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("HF Space FLUX.1-schnell unavailable")


def _pollinations_flux(prompt: str, max_bytes: int = _DEFAULT_MAX_IMAGE_BYTES) -> bytes:
    """Free open-source FLUX image generation fallback requiring no credits."""
    import urllib.parse
    import urllib.request

    url = (
        "https://image.pollinations.ai/prompt/"
        + urllib.parse.quote(prompt)
        + "?model=flux&width=512&height=512&nologo=true"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ToolError(f"response exceeded size limit ({max_bytes} bytes)")
        return raw


class _GeneratedImageBytes(bytes):
    """`bytes` subclass carrying the actual `(provider, model)` that produced the image."""

    provider: str
    model: str

    def __new__(cls, raw: bytes, provider: str, model: str) -> _GeneratedImageBytes:
        obj = super().__new__(cls, raw)
        obj.provider = provider
        obj.model = model
        return obj


def _hf_post_image(model: str, api_key: str, prompt: str, *args: Any, max_bytes: int = _DEFAULT_MAX_IMAGE_BYTES, **kwargs: Any) -> bytes:
    """Generate an image using open-weights FLUX models (best-effort public/community endpoints;
    rate-limited and subject to queue availability, never guaranteed or unlimited).

    1. Tries the official `black-forest-labs/FLUX.1-schnell` Hugging Face ZeroGPU Space
       (records `provider="huggingface"`, `model="black-forest-labs/FLUX.1-schnell"`).
    2. Falls back to the Pollinations open-weights FLUX endpoint
       (records `provider="pollinations"`, `model="flux"` — never mislabelled as Hugging Face).
    3. Falls back to Hugging Face Inference Router (`nscale` / `hf-inference`) when `HF_TOKEN` is set.
    """
    import urllib.error
    import urllib.request

    space_err: Exception | None = None
    poll_err: Exception | None = None

    # 1. Public Hugging Face Space (ZeroGPU) for black-forest-labs/FLUX.1-schnell (rate-limited)
    try:
        try:
            raw = _hf_space_flux_schnell(prompt, api_key=api_key, max_bytes=max_bytes)
        except TypeError:
            raw = _hf_space_flux_schnell(prompt, api_key=api_key)
        if raw:
            return _GeneratedImageBytes(raw, provider="huggingface", model="black-forest-labs/FLUX.1-schnell")
    except Exception as exc:  # noqa: BLE001
        space_err = exc

    # 2. Secondary open-weights fallback via Pollinations (rate-limited; labelled provider="pollinations")
    try:
        try:
            raw = _pollinations_flux(prompt, max_bytes=max_bytes)
        except TypeError:
            raw = _pollinations_flux(prompt)
        if raw:
            m = re.search(rb'"actualModel"\s*:\s*"([A-Za-z0-9._\-/]+)"', raw[:2048])
            actual_poll_model = m.group(1).decode("ascii") if m else "flux"
            return _GeneratedImageBytes(raw, provider="pollinations", model=actual_poll_model)
    except Exception as exc:  # noqa: BLE001
        poll_err = exc

    clean_model = model.strip("/")

    def _make_http_error(exc: urllib.error.HTTPError) -> RuntimeError:
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        err = RuntimeError(f"{exc.code} {exc.reason} {err_body}".strip())
        err.code = exc.code  # type: ignore[attr-defined]
        return err

    # 3. HF Router provider (`nscale` / `hf-inference`) when an API token is configured
    if not api_key:
        detail = f"hf-space ({space_err}), pollinations ({poll_err})"
        raise RuntimeError(f"public FLUX endpoints temporarily unavailable ({detail}) and HF_TOKEN is not set")
    nscale_url = "https://router.huggingface.co/nscale/v1/images/generations"
    nscale_body = json.dumps({
        "response_format": "b64_json",
        "prompt": prompt,
        "model": clean_model,
    }).encode("utf-8")
    nscale_req = urllib.request.Request(
        nscale_url,
        data=nscale_body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    nscale_err: RuntimeError | None = None
    try:
        with urllib.request.urlopen(nscale_req, timeout=30) as resp:
            raw = resp.read()
        parsed = json.loads(raw.decode("utf-8"))
        b64_data = parsed["data"][0]["b64_json"]
        return _GeneratedImageBytes(base64.b64decode(b64_data), provider="huggingface", model=clean_model)
    except urllib.error.HTTPError as exc:
        nscale_err = _make_http_error(exc)
        if exc.code not in (400, 402, 404, 410, 422):
            raise nscale_err from None
    except (KeyError, IndexError, ValueError, TypeError):
        pass

    hf_url = f"https://router.huggingface.co/hf-inference/models/{clean_model}"
    hf_body = json.dumps({"inputs": prompt}).encode("utf-8")
    hf_req = urllib.request.Request(
        hf_url,
        data=hf_body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "image/png",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(hf_req, timeout=30) as resp:
            return _GeneratedImageBytes(resp.read(), provider="huggingface", model=clean_model)
    except urllib.error.HTTPError as exc:
        hf_err = _make_http_error(exc)
        if exc.code == 410 and nscale_err is not None:
            raise nscale_err from None
        raise hf_err from None


def image_generate_huggingface(payload: Payload, params: dict[str, Any]) -> Payload:
    """Real image generation via open-weights FLUX (`black-forest-labs/FLUX.1-schnell`).
    Uses best-effort public endpoints (Hugging Face ZeroGPU Space, with Pollinations FLUX fallback)
    and optional `HF_TOKEN` Router fallback; subject to public queue availability and rate limits."""
    prompt = (payload.text or "").strip()
    if not prompt:
        raise MissingInputError("no prompt for image generation")
    values = merged_env()
    api_key = (values.get("HF_TOKEN") or values.get("HUGGINGFACEHUB_API_TOKEN") or "").strip()
    model = (
        str(params.get("model") or values.get("AGENT_REPO_HF_IMAGE_MODEL", _DEFAULT_HF_IMAGE_MODEL)).strip()
        or _DEFAULT_HF_IMAGE_MODEL
    )
    try:
        max_bytes = int(str(values.get("AGENT_REPO_API_MAX_IMAGE_BYTES", str(_DEFAULT_MAX_IMAGE_BYTES))))
    except (TypeError, ValueError):
        max_bytes = _DEFAULT_MAX_IMAGE_BYTES
    try:
        generated = _hf_post_image(model, api_key, prompt)
    except Exception as exc:  # noqa: BLE001 - provider failure -> clean ToolError
        raise ToolError(
            _sanitize_provider_error(exc, prompt=prompt, prefix="huggingface image generation failed: "),
            retryable=_is_retryable_provider_error(exc),
        ) from exc
    if not generated:
        raise ToolError("provider returned no image")
    actual_provider = getattr(generated, "provider", "huggingface")
    actual_model = getattr(generated, "model", model)
    image_bytes = bytes(generated)
    mime = _image_mime_from_magic(image_bytes)
    if len(image_bytes) > max_bytes:
        raise ToolError("generated image exceeds size cap")
    return Payload(
        modality=Modality.IMAGE,
        text=f"[{mime}, {len(image_bytes)} bytes, generated by {actual_provider}:{actual_model}]",
        data={
            "format": mime.split("/")[-1],
            "content": image_bytes,
            "stub": False,
            "provider": actual_provider,
            "model": actual_model,
            "mime": mime,
        },
    )


def builtin_registry(clock: Callable[[], datetime] | None = None,
                     tool_settings: ToolSettings | None = None) -> ToolRegistry:
    """Build the registry. `tool_settings` selects real providers per tool; defaults to all-stub."""
    reg = ToolRegistry(clock=clock) if clock else ToolRegistry()
    ts = tool_settings or ToolSettings()
    t, a, i, f, j = Modality.TEXT, Modality.AUDIO, Modality.IMAGE, Modality.FILE, Modality.JSON
    if ts.transcriber == "groq":
        asr = ToolSpec("audio_transcribe", "Speech-to-text via Groq (whisper)", RiskTier.LOW, (a,), t, False,
                       audio_transcribe_groq)
    else:
        asr = ToolSpec("audio_transcribe", "STUB speech-to-text", RiskTier.LOW, (a,), t, True,
                       audio_transcribe_stub)
    if ts.image_classifier == "gemini":
        classifier = ToolSpec("image_classify", "Image labelling via Gemini vision", RiskTier.LOW, (i,), j, False,
                              image_classify_gemini)
    else:
        classifier = ToolSpec("image_classify", "STUB image labels", RiskTier.LOW, (i,), j, True,
                              image_classify_stub)
    if ts.image_generator == "gemini":
        generator = ToolSpec("image_generate", "Real image generation via Gemini/Imagen", RiskTier.MEDIUM, (t,), i, False,
                             image_generate_gemini)
    elif ts.image_generator in ("huggingface", "hf"):
        generator = ToolSpec("image_generate", "Real image generation via Hugging Face (FLUX.1-schnell)", RiskTier.MEDIUM, (t,), i, False,
                             image_generate_huggingface)
    else:
        generator = ToolSpec("image_generate", "STUB image generation (SVG placeholder)", RiskTier.MEDIUM, (t,), i, True,
                             image_generate_stub)
    for spec in (
        ToolSpec("text_summarize", "Extractive text summary", RiskTier.LOW, (t,), t, False, text_summarize),
        ToolSpec("file_analyze", "Statistics for text/CSV/binary files", RiskTier.LOW, (f,), j, False, file_analyze),
        asr,
        classifier,
        generator,
        ToolSpec("email_send", "STUB email sender (never sends)", RiskTier.HIGH, (t,), j, True, email_send_stub),
    ):
        reg.register(spec)
    return reg

