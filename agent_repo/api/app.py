"""FastAPI app factory and routes for the local read-mostly API.

Nothing here re-implements routing, repository, feedback, execution or privacy. The app calls
the existing `agent_repo` core (via `agent_repo.api.views` and `agent_repo.api.support`, which reuse the CLI's
_safe_text`/`_make_store`) and exposes a small JSON surface.

Security posture:
- ``create_app`` takes a ``store_provider`` so tests inject a seeded in-memory graph. In real
  runs the backend is chosen once at startup by CLI argument/env only, never per request.
- Read-only except ``POST /ask``. There is intentionally no approve/reject/rollback/feedback/
  delete route; ``test_api_routes.py`` asserts the exact route set.
- All strings pass through CLI scrubbing. Errors are sanitized like the CLI; unknown ids 404.
- Uploads: 1 MB cap (configurable), generated temp names, deleted after the run, 413 over cap.
- ``POST /ask`` is serialized with a lock (single-process in-memory graph).
- No CORS origins enabled by default.
"""

from __future__ import annotations

import json
import re
import threading
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from pathlib import Path

from agent_repo.actors import NotHumanError
from agent_repo.api import views
from agent_repo.api.models import (
    AgentDetail,
    AgentRow,
    AskResponse,
    ExecutionDetail,
    ExecutionRow,
    Health,
    ProposalsResponse,
    VersionsResponse,
)
from agent_repo.api import support as api_support
from agent_repo.cli import CLIError
from agent_repo.config import Settings
from agent_repo.execution.runner import ExecutionResult, Runner
from agent_repo.feedback import FeedbackError
from agent_repo.graph.store import GraphError, GraphStore
from agent_repo.llm import LLMError
from agent_repo.repository import AgentRepository, RepositoryError
from agent_repo.tools import ToolError


DEFAULT_MAX_UPLOAD_BYTES = 1024 * 1024
DEFAULT_MAX_OUTPUT_BYTES = views.DEFAULT_MAX_OUTPUT_BYTES

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_UI_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

_EXPECTED_ERRORS = (
    CLIError, FeedbackError, RepositoryError, GraphError, NotHumanError, ValidationError,
    ValueError, OSError, KeyError, ToolError, LLMError,
)


def _sanitize(exc: BaseException, settings: Settings) -> str:
    """Same wording the CLI boundary prints (never a raw stack trace)."""
    if isinstance(exc, _EXPECTED_ERRORS):
        return api_support.safe(str(exc), settings)
    return f"{type(exc).__name__}: {api_support.safe(str(exc), settings)}"


_FILENAME_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_upload_filename(file_name: str) -> str:
    """Return a display-safe basename for an uploaded file.

    - Strips directory components (basename only).
    - Strips control characters.
    - Caps at 100 characters.
    Returns an empty string when no name was provided.
    """
    if not file_name:
        return ""
    base = file_name.replace("\\", "/").split("/").pop()
    base = _FILENAME_CONTROL_RE.sub("", base)
    return base[:100]


StoreProvider = Callable[[], GraphStore]


def create_app(
    store_provider: StoreProvider,
    *,
    target: str = "in-memory",
    settings: Settings | None = None,
    settings_provider: Callable[[], Settings] | None = None,
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    runner_factory: Callable[[AgentRepository], Runner] | None = None,
) -> FastAPI:
    """Build the app. ``store_provider`` supplies the graph; tests inject an in-memory seed.

    ``target`` is a credential-free label reported by ``GET /health`` ("file <path>", "Aura", or
    "in-memory"); it is decided at startup, never from a request. ``settings_provider`` (or a
    fixed ``settings``) is evaluated lazily so a broken ``.env`` surfaces as a sanitized error
    rather than crashing app construction.
    """

    state: dict[str, object] = {"settings": settings, "repo": None, "store": None}
    lock = threading.Lock()

    def get_settings() -> Settings:
        if state["settings"] is None:
            state["settings"] = settings_provider() if settings_provider else Settings.from_env()
        return state["settings"]  # type: ignore[return-value]

    def get_repo() -> AgentRepository:
        if state["repo"] is None:
            store = store_provider()
            state["store"] = store
            state["repo"] = views.build_repo(store, get_settings())
        return state["repo"]  # type: ignore[return-value]

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Build the repository once eagerly so a startup failure is visible; no network calls.
        get_settings()
        get_repo()
        yield
        store = state["store"]
        close = getattr(store, "close", None)
        if callable(close):
            close()

    app = FastAPI(title="agent-repo local API", version="0.1.0", lifespan=lifespan)
    from fastapi.exceptions import RequestValidationError  # locally so fastapi 0.156.4 import works

    # --- Static UI (placeholder /ui/). Serves only files inside agent_repo/api/static/. ---
    @app.get("/ui/")
    def ui_index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html", media_type="text/html")

    @app.get("/ui/static/{file_path:path}")
    def ui_static(file_path: str) -> FileResponse:
        target = (_STATIC_DIR / file_path).resolve()
        if not target.is_file() or _STATIC_DIR.resolve() not in target.parents:
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(target)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/ui/" or request.url.path.startswith("/ui/static/"):
            for name, value in _UI_HEADERS.items():
                response.headers[name] = value
        return response

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, _exc: Exception) -> JSONResponse:  # pragma: no cover
        return JSONResponse(status_code=500, content={"detail": "Internal error."})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        """Never echo the submitted value; return a JSON list of field/message objects."""
        settings = Settings.from_env()
        detail: list[dict[str, str]] = []
        for error in exc.errors():
            loc = error.get("loc") or ()
            field = str(loc[-1]) if loc else "request"
            message = api_support.safe(str(error.get("msg", "invalid")), settings)
            detail.append({"field": field, "message": message})
        return JSONResponse(status_code=422, content={"detail": detail})

    @app.get("/health", response_model=Health)
    def health() -> Health:
        return Health(status="ok", target=target)

    @app.get("/agents", response_model=list[AgentRow])
    def list_agents() -> list[AgentRow]:
        return views.agent_rows(get_repo(), get_settings())

    @app.get("/agents/{name}", response_model=AgentDetail)
    def get_agent(name: str) -> AgentDetail:
        try:
            return views.agent_detail(get_repo(), name, get_settings())
        except (RepositoryError, KeyError) as exc:
            raise HTTPException(status_code=404, detail="agent not found") from exc

    @app.get("/agents/{name}/versions", response_model=VersionsResponse)
    def get_versions(name: str) -> VersionsResponse:
        repo = get_repo()
        settings = get_settings()
        try:
            default = repo.default_version(name)
            return VersionsResponse(
                agent=api_support.safe(name, settings),
                default=api_support.safe(default.version, settings) if default else None,
                versions=views.version_rows(repo, name, settings),
                lineage=views.lineage_rows(repo, name, settings),
            )
        except (RepositoryError, KeyError) as exc:
            raise HTTPException(status_code=404, detail="version not found") from exc

    @app.get("/executions", response_model=list[ExecutionRow])
    def list_executions(limit: int = 25) -> list[ExecutionRow]:
        if limit < 1 or limit > 200:
            raise HTTPException(
                status_code=422,
                detail=[{"field": "limit", "message": "limit must be between 1 and 200 (got " + str(limit) + ")."}],
            )
        limit = max(1, min(int(limit), 200))
        return views.execution_rows(get_repo(), get_settings(), limit)

    @app.get("/executions/{execution_id}", response_model=ExecutionDetail)
    def get_execution(execution_id: str) -> ExecutionDetail:
        try:
            return views.execution_detail(get_repo(), execution_id, get_settings())
        except (KeyError, GraphError) as exc:
            raise HTTPException(status_code=404, detail="execution not found") from exc

    @app.get("/executions/{execution_id}/artifact")
    def get_execution_artifact(execution_id: str, request: Request) -> Response:
        repo = get_repo()
        node = repo.graph.get_node(execution_id)
        if node is None or node.label != "Execution":
            raise HTTPException(status_code=404, detail="execution not found")
        artifact_id = str(node.props.get("artifact_id", ""))
        if not artifact_id:
            raise HTTPException(status_code=404, detail="artifact not found")
        artifact_owner = str(node.props.get("artifact_owner", "user"))
        expected_ref = str(node.props.get("artifact_ref", "")) or None

        requester_header = request.headers.get("x-agent-repo-actor")
        requester = requester_header.strip() if requester_header else None

        if artifact_owner and artifact_owner != "user":
            if not requester or requester != artifact_owner:
                raise HTTPException(status_code=403, detail="permission denied for artifact")
        elif requester is not None and requester != "user":
            raise HTTPException(status_code=403, detail="permission denied for artifact")

        settings = get_settings()
        from agent_repo.artifacts import (
            ArtifactAccessError, ArtifactError, ArtifactNotFoundError, get_default_artifact_store,
        )
        store = get_default_artifact_store(settings)
        try:
            raw, mime = store.load_image(
                artifact_id,
                expected_ref=expected_ref,
                expected_owner=artifact_owner,
                requester=requester,
            )
        except ArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="artifact not found") from exc
        except ArtifactAccessError as exc:
            raise HTTPException(status_code=403, detail="permission denied for artifact") from exc
        except ArtifactError as exc:
            raise HTTPException(status_code=400, detail=f"artifact error: {exc}") from exc

        headers = {
            **_UI_HEADERS,
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'inline; filename="{artifact_id}.{mime.split("/")[-1]}"',
        }
        return Response(content=raw, media_type=mime, headers=headers)

    @app.get("/proposals", response_model=ProposalsResponse)
    def list_proposals() -> ProposalsResponse:
        return ProposalsResponse(proposals=views.proposal_rows(get_repo(), get_settings()))

    @app.post("/ask", response_model=AskResponse)
    async def ask(query: str = Form(...), attachment: UploadFile | None = None) -> AskResponse:
        settings = get_settings()
        repo = get_repo()
        data: bytes | None = None
        upload_filename = ""
        if attachment is not None:
            data = await attachment.read()
            if len(data) > max_upload_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"Error: attachment exceeds the {max_upload_bytes}-byte upload limit.",
                )
            upload_filename = sanitize_upload_filename(attachment.filename or "")
        with lock:
            try:
                if data is not None:
                    with api_support.temp_upload(attachment.filename or "", data) as temp_path:
                        result = _run(repo, settings, query, temp_path, runner_factory)
                else:
                    result = _run(repo, settings, query, None, runner_factory)
            except _EXPECTED_ERRORS as exc:
                raise HTTPException(status_code=400, detail=f"Error: {_sanitize(exc, settings)}") from exc
        response = views.ask_response(repo, result, settings, max_output_bytes)
        response.upload_filename = upload_filename or None
        return response

    return app


def service_routes(app: FastAPI) -> set[tuple[str, str]]:
    """(method, path) pairs, excluding FastAPI's auto docs/OpenAPI routes."""
    return {
        (method, route.path)
        for route in app.routes
        if hasattr(route, "methods")
        for method in route.methods
        if method not in {"HEAD", "OPTIONS"}
        and not route.path.startswith(("/docs", "/openapi", "/redoc"))
    }


def _run(repo: AgentRepository, settings: Settings, query: str, attachment_path: str | None,
         runner_factory: Callable[[AgentRepository], Runner] | None) -> ExecutionResult:
    if runner_factory:
        runner = runner_factory(repo)
    else:
        from agent_repo.artifacts import get_default_artifact_store
        runner = Runner(repo, artifact_store=get_default_artifact_store(settings))
    if attachment_path and Path(attachment_path).suffix.lower() == ".docx":
        from agent_repo.manifest import Modality
        from agent_repo.routing import BreakReason, extract_facts
        from agent_repo.tools import Payload, _extract_docx_text

        docx_text = _extract_docx_text(Path(attachment_path).read_bytes())
        if docx_text:
            facts = extract_facts(query)
            sum_rec = repo.default_version("text_summarizer")
            fa_rec = repo.default_version("file_analyzer")
            sum_score = runner.router.score_agent(facts, sum_rec)[0] if sum_rec else 0.0
            fa_score = runner.router.score_agent(facts, fa_rec)[0] if fa_rec else 0.0
            if sum_score > fa_score and sum_score >= runner.router.config.min_score_threshold:
                return runner.execute_query(
                    query,
                    payload=Payload(modality=Modality.TEXT, text=f"summarize: {docx_text}"),
                )
        result = runner.execute_query(query, attachment_path=attachment_path)
        if result.status == "broken" and result.break_reason is BreakReason.MISSING_INPUT and docx_text:
            return runner.execute_query(
                query,
                payload=Payload(modality=Modality.TEXT, text=f"summarize: {docx_text}"),
            )
        return result
    return runner.execute_query(query, attachment_path=attachment_path)
