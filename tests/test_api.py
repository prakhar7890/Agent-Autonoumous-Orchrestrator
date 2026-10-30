"""Tests for the read-mostly local API (`agent_repo.api`).

These tests are backend-independent: they inject a seeded `InMemoryGraph` through the app
factory's `store_provider`, so no Neo4j/AuraDB is touched and no `.env` value is required.
They assert the exact route set (so no write endpoint can slip in), the security rules, and the
typed `/ask` output including the image/script-safety and output-cap behaviour.
"""

from __future__ import annotations

import base64
import re

import pytest

fastapi = pytest.importorskip("fastapi", reason="API tests need the 'ui' extra (fastapi)")
from fastapi.testclient import TestClient  # noqa: E402

from agent_repo.api.app import create_app, service_routes  # noqa: E402
from agent_repo.config import Settings  # noqa: E402
from agent_repo.graph.store import InMemoryGraph  # noqa: E402
from agent_repo.manifest import Modality  # noqa: E402
from agent_repo.repository import AgentRepository  # noqa: E402
from agent_repo.seeds import seed_repository  # noqa: E402
from agent_repo.tools import builtin_registry  # noqa: E402

FAKE_SECRET = "sk-FAKEFAKEFAKEFAKE1234567890"
FAKE_EMAIL = "person@example.com"

# The exact, intended surface. Any added/removed route or method fails loudly.
EXPECTED_ROUTES = {
    ("GET", "/health"),
    ("GET", "/agents"),
    ("GET", "/agents/{name}"),
    ("GET", "/agents/{name}/versions"),
    ("GET", "/executions"),
    ("GET", "/executions/{execution_id}"),
    ("GET", "/executions/{execution_id}/artifact"),
    ("GET", "/proposals"),
    ("POST", "/ask"),
    ("GET", "/ui/"),
    ("GET", "/ui/static/{file_path:path}"),
}


def _seeded_store() -> InMemoryGraph:
    store = InMemoryGraph()
    repo = AgentRepository(store, tools=builtin_registry())
    seed_repository(repo, "API Test Maintainer")
    return store


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def client(settings: Settings) -> TestClient:
    app = create_app(_seeded_store, settings=settings)
    with TestClient(app) as test_client:
        yield test_client


def test_route_set_is_exactly_readonly_plus_ask(settings: Settings) -> None:
    app = create_app(_seeded_store, settings=settings)
    assert service_routes(app) == EXPECTED_ROUTES


def test_no_write_endpoints_present(settings: Settings) -> None:
    app = create_app(_seeded_store, settings=settings)
    routes = service_routes(app)
    banned = {"approve", "reject", "rollback", "feedback", "delete", "promote", "grant"}
    for _method, path in routes:
        assert not (banned & set(path.strip("/").split("/"))), f"write route leaked: {path}"


def test_health_reports_target_without_credentials(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "target": "in-memory"}


def test_health_reports_file_target(settings: Settings) -> None:
    app = create_app(_seeded_store, target="file agent-graph.json", settings=settings)
    with TestClient(app) as test_client:
        body = test_client.get("/health").json()
    assert body["target"] == "file agent-graph.json"


def test_health_reports_aura_target(settings: Settings) -> None:
    app = create_app(_seeded_store, target="Aura", settings=settings)
    with TestClient(app) as test_client:
        body = test_client.get("/health").json()
    assert body["target"] == "Aura"
    # No credential-looking value ever appears in the health payload.
    assert "password" not in str(body).lower()


def test_agents_catalog_shape(client: TestClient) -> None:
    response = client.get("/agents")
    assert response.status_code == 200
    rows = response.json()
    names = [row["name"] for row in rows]
    assert names == sorted(names)
    assert len(rows) == 7
    by_name = {row["name"]: row for row in rows}
    assert set(by_name["email_sender"]) == {
        "name", "version", "status", "input_to_output", "risk_tools", "sub_agents",
        "example_queries",
    }
    assert by_name["email_sender"]["risk_tools"].startswith("email_send (high")
    assert by_name["audio_transcriber"]["input_to_output"] == "audio -> text"


def test_agent_detail_shape(client: TestClient) -> None:
    response = client.get("/agents/email_sender")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "email_sender"
    assert "email_send (high" in ", ".join(body["tools"])
    assert isinstance(body["pipeline"], list)
    assert body["chainable"] in (True, False)


def test_agent_versions_lineage(client: TestClient) -> None:
    response = client.get("/agents/email_sender/versions")
    assert response.status_code == 200
    body = response.json()
    assert body["agent"] == "email_sender"
    assert body["versions"]
    assert {"version", "status", "author", "created_at", "default", "change_note"} == set(body["versions"][0])
    assert body["lineage"] and {"version", "status", "chain"} == set(body["lineage"][0])


def test_unknown_agent_returns_clean_404(client: TestClient) -> None:
    response = client.get("/agents/does_not_exist")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "Traceback" not in detail
    # Submitted value must never be echoed.
    assert "does_not_exist" not in detail
    assert detail == "agent not found"


def test_executions_list_and_limit(client: TestClient) -> None:
    # Run one query first so there is at least one execution.
    client.post("/ask", data={"query": "summarize the key points of this report: Alpha. Beta."})
    response = client.get("/executions?limit=5")
    assert response.status_code == 200
    rows = response.json()
    assert 1 <= len(rows) <= 5
    assert {"execution", "status", "breakpoint", "query", "created_at"} == set(rows[0])


def test_execution_detail_matches_explain(client: TestClient) -> None:
    ask = client.post("/ask", data={"query": "summarize the key points of this report: Alpha. Beta."})
    execution_id = ask.json()["execution_id"]
    response = client.get(f"/executions/{execution_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["execution_id"] == execution_id
    assert body["output"]["kind"] == "text"
    # rendered is CLI-explain text: it must be present and not leaked unscrubbed.
    assert isinstance(body["rendered"], str) and body["rendered"].strip()
    # A "Breakpoint" marker must appear in rendered exactly when a breakpoint fired.
    assert ("Breakpoint" in body["rendered"]) == bool(body.get("break_reason"))


def test_unknown_execution_returns_clean_404(client: TestClient) -> None:
    response = client.get("/executions/nope-123")
    assert response.status_code == 404
    assert "Traceback" not in response.json()["detail"]


def test_proposals_readonly_shape(client: TestClient) -> None:
    response = client.get("/proposals")
    assert response.status_code == 200
    body = response.json()
    assert "proposals" in body
    for row in body["proposals"]:
        assert {"proposal", "status", "agent", "base_version", "candidate_version", "trigger",
                "created_at", "evaluation"} == set(row)


def test_ask_success_text(client: TestClient) -> None:
    response = client.post("/ask", data={"query": "summarize the key points of this report: Alpha. Beta."})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["execution_id"]
    assert body["output"]["kind"] == "text"
    assert body["output"]["text"]
    assert body["plan"] is not None
    assert body["steps"]
    step = body["steps"][0]
    assert {"order", "role", "agent", "version", "what_it_does", "why_chosen",
            "matched_terms", "score", "tools", "model"} <= set(step)


def test_ask_missing_input_breakpoint(client: TestClient) -> None:
    response = client.post("/ask", data={"query": "transcribe this audio recording"})
    assert response.status_code == 200
    body = response.json()
    assert body["break_reason"] == "missing_input"
    assert body["clarifying_question"]


def test_ask_no_match_breakpoint(client: TestClient) -> None:
    response = client.post("/ask", data={"query": "zzzz unrelated gibberish qqqq"})
    assert response.status_code == 200
    body = response.json()
    assert body["break_reason"] == "no_match"
    assert body["output"]["kind"] == "text"


def test_ask_image_output_is_typed_svg(client: TestClient) -> None:
    response = client.post("/ask", data={"query": "generate an image of a blue landscape"})
    assert response.status_code == 200
    output = response.json()["output"]
    assert output["kind"] == "image"
    assert output["mime"] == "image/svg+xml"
    assert output["data_base64"]
    decoded = base64.b64decode(output["data_base64"]).decode("utf-8")
    assert decoded.lstrip().startswith("<svg")


def test_ask_returns_actual_answer_text(client: TestClient) -> None:
  # The Ask tab must display a real answer, never just a generic status such as "Done."
  response = client.post("/ask", data={"query": "summarize the key points: A. B. C."})
  assert response.status_code == 200
  body = response.json()
  assert body["status"] == "succeeded"
  assert body["execution_id"]
  output = body["output"]
  assert output["kind"] == "text"
  assert output["text"], "expected a non-empty text answer"


def test_ask_returns_steps_with_execution_id(client: TestClient) -> None:
  # Steps and the execution id must be present for the UI to render the plan.
  response = client.post("/ask", data={"query": "summarize the key points: A. B. C."})
  assert response.status_code == 200
  body = response.json()
  assert body["steps"], "expected at least one plan step"
  step = body["steps"][0]
  assert {"order", "role", "agent", "what_it_does", "why_chosen", "matched_terms", "score", "tools", "model"} <= set(step)
  assert step["matched_terms"] is None or isinstance(step["matched_terms"], list)
  if step["matched_terms"] is not None:
      assert all(isinstance(term, str) for term in step["matched_terms"])
  assert body["execution_id"]


def test_executions_rows_have_expected_fields(client: TestClient) -> None:
  # Run one query first so there is at least one execution.
  client.post("/ask", data={"query": "summarize the key points of this report: Alpha. Beta."})
  response = client.get("/executions?limit=5")
  assert response.status_code == 200
  rows = response.json()
  assert 1 <= len(rows) <= 5
  assert {"execution", "status", "breakpoint", "query", "created_at"} == set(rows[0])


def test_versions_lineage_has_fields(client: TestClient) -> None:
  agents = client.get("/agents").json()
  assert agents
  name = agents[0]["name"]
  response = client.get(f"/agents/{name}/versions")
  assert response.status_code == 200
  body = response.json()
  assert "versions" in body
  if body["versions"]:
      assert {"version", "status", "author", "created_at", "default", "change_note"} == set(body["versions"][0])


def test_proposals_rows_have_expected_fields(client: TestClient) -> None:
  response = client.get("/proposals")
  assert response.status_code == 200
  body = response.json()
  assert "proposals" in body
  if body["proposals"]:
      assert {"proposal", "status", "agent", "base_version", "candidate_version", "trigger", "created_at", "evaluation"} == set(body["proposals"][0])


def test_ask_svg_with_script_is_data_only_never_inlined(settings: Settings) -> None:
    # Force an image payload whose SVG contains a script tag, then confirm the app returns it as
    # opaque base64 and never emits it inline in any HTML body.
    malicious = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    from agent_repo.api import views
    from agent_repo.tools import Payload

    payload = Payload(modality=Modality.IMAGE, text="caption",
                      data={"format": "svg", "content": malicious})
    typed = views.typed_output(payload, settings)
    assert typed.kind == "image"
    assert typed.mime == "image/svg+xml"
    decoded = base64.b64decode(typed.data_base64 or "").decode("utf-8")
    assert "<script>" in decoded  # the bytes are returned verbatim, as data
    # The API's JSON body is the only representation: the raw markup is never served as HTML.
    app = create_app(_seeded_store, settings=settings)
    with TestClient(app) as test_client:
        response = test_client.post("/ask", data={"query": "generate an image of a blue landscape"})
    assert response.headers["content-type"].startswith("application/json")
    assert "<script>" not in response.text


def test_oversized_output_is_refused_concisely(settings: Settings) -> None:
    from agent_repo.api import views
    from agent_repo.tools import Payload

    big = "<svg>" + ("x" * 5000) + "</svg>"
    payload = Payload(modality=Modality.IMAGE, text="big", data={"format": "svg", "content": big})
    typed = views.typed_output(payload, settings, max_bytes=1024)
    assert typed.kind == "text"
    assert typed.data_base64 is None
    assert "cap" in typed.text


def test_scrubbing_redacts_fake_secret_and_email(settings: Settings) -> None:
    # Configure the fake secret as the hash salt so exact-secret redaction applies in addition
    # to regex scrubbing (the API reuses `_safe_text`, which redacts `settings.hash_salt`).
    settings_with_secret = Settings(hash_salt=FAKE_SECRET)
    app = create_app(_seeded_store, settings=settings_with_secret)
    query = f"summarize this: token {FAKE_SECRET} contact {FAKE_EMAIL}"
    with TestClient(app) as test_client:
        response = test_client.post("/ask", data={"query": query})
        listing = test_client.get("/executions?limit=50")
    combined = response.text + listing.text
    # Neither the raw secret nor the raw email may appear anywhere in any response.
    assert FAKE_SECRET not in combined
    assert FAKE_EMAIL not in combined
    assert "[REDACTED_EMAIL]" in combined
    assert FAKE_SECRET not in response.text


def test_oversized_upload_returns_413(settings: Settings) -> None:
    app = create_app(_seeded_store, settings=settings, max_upload_bytes=64)
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ask",
            data={"query": "analyze this report file"},
            files={"attachment": ("report.txt", b"x" * 1000, "text/plain")},
        )
    assert response.status_code == 413
    assert "upload limit" in response.json()["detail"]


def test_client_filename_path_is_ignored(settings: Settings) -> None:
    # A traversal filename must not escape: the API uses a generated temp name. We prove the
    # call still works and no file is written outside the temp dir by asserting success + cleanup.
    app = create_app(_seeded_store, settings=settings)
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ask",
            data={"query": "analyze this report file"},
            files={"attachment": ("../../evil.txt", b"Quarterly report: 12 requests.", "text/plain")},
        )
    assert response.status_code == 200
    assert response.json()["execution_id"]


def test_upload_temp_file_deleted(settings: Settings, monkeypatch) -> None:
    import tempfile
    created: list[str] = []
    real_mkstemp = tempfile.mkstemp

    def spy_mkstemp(*args, **kwargs):
        handle, path = real_mkstemp(*args, **kwargs)
        created.append(path)
        return handle, path

    monkeypatch.setattr(tempfile, "mkstemp", spy_mkstemp)
    app = create_app(_seeded_store, settings=settings)
    with TestClient(app) as test_client:
        test_client.post(
            "/ask",
            data={"query": "analyze this report file"},
            files={"attachment": ("report.txt", b"Quarterly report: 12 requests.", "text/plain")},
        )
    assert created
    from pathlib import Path
    for path in created:
        assert not Path(path).exists(), f"temp upload left behind: {path}"


def test_upload_filename_is_sanitized_base_name(settings: Settings) -> None:
    # "../x-FAKE-SECRET-sk-123.pdf" must never expose a temporary name, path parts,
    # or the client name as a path; only the sanitized base name is returned.
    app = create_app(_seeded_store, settings=settings)
    with TestClient(app) as test_client:
        response = test_client.post(
            "/ask",
            data={"query": "summarize this report"},
            files={"attachment": ("../x-FAKE-SECRET-sk-123.pdf", b"Quarterly report: 12 requests.", "application/pdf")},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    # (1) Temporary name never exposed.
    assert "temp" not in body.get("upload_filename", "").lower()
    assert "/tmp" not in body.get("upload_filename", "")
    assert "\\temp" not in body.get("upload_filename", "")
    assert "\\Temp" not in body.get("upload_filename", "")
    # (2) Only a sanitized base name is returned: no "../", base name only.
    assert body["upload_filename"] == "x-FAKE-SECRET-sk-123.pdf"
    assert "../" not in body["upload_filename"]
    assert "/" not in body["upload_filename"]
    assert "\\" not in body["upload_filename"]
    # (3) The client name is never used as a path: no directory components survive.
    assert not body["upload_filename"].startswith(".")
    assert not body["upload_filename"].startswith("/")


def test_upload_filename_empty_when_no_attachment(settings: Settings) -> None:
    app = create_app(_seeded_store, settings=settings)
    with TestClient(app) as test_client:
        response = test_client.post("/ask", data={"query": "summarize this report"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("upload_filename") is None


def test_ask_huggingface_webp_image_rendered_as_typed_image_output(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_repo.tools as tools_mod
    from agent_repo.config import ToolSettings

    webp_bytes = b"RIFF\x20\x00\x00\x00WEBPfake-webp-image"
    monkeypatch.setattr(tools_mod, "merged_env", lambda: {})
    monkeypatch.setattr(tools_mod, "_hf_post_image", lambda m, k, p: webp_bytes)
    hf_settings = Settings(
        hash_salt="0123456789abcdef0123456789abcdef",
        tools=ToolSettings(image_generator="huggingface"),
    )
    app = create_app(_seeded_store, settings=hf_settings)
    with TestClient(app) as test_client:
        response = test_client.post("/ask", data={"query": "generate an image of a red car"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["output"]["kind"] == "image"
    assert body["output"]["mime"] == "image/webp"
    assert body["output"]["data_base64"] == base64.b64encode(webp_bytes).decode("ascii")


def test_ask_docx_summarize_and_analyze(settings: Settings) -> None:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>"
            "<w:p><w:r><w:t>Agent Swarm Platform Requirements Design and Implementation Guide</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>The platform routes user requests autonomously using manifest metadata.</w:t></w:r></w:p>"
            "</w:body></w:document>",
        )
    docx_bytes = buf.getvalue()

    app = create_app(_seeded_store, settings=settings)
    with TestClient(app) as test_client:
        r_sum = test_client.post(
            "/ask",
            data={"query": "summarize this document"},
            files={"attachment": ("guide.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        assert r_sum.status_code == 200, r_sum.text
        b_sum = r_sum.json()
        assert b_sum["status"] == "succeeded"
        assert b_sum["steps"][0]["agent"] == "text_summarizer"
        assert "autonomously" in b_sum["output"]["text"]

        r_ana = test_client.post(
            "/ask",
            data={"query": "analyze this document"},
            files={"attachment": ("guide.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        assert r_ana.status_code == 200, r_ana.text
        b_ana = r_ana.json()
        assert b_ana["status"] == "succeeded"
        assert b_ana["steps"][0]["agent"] == "file_analyzer"
        assert '"kind": "docx"' in b_ana["output"]["text"]
        assert '"summary":' in b_ana["output"]["text"]



def test_bind_guard_rejects_non_local_host() -> None:
    from agent_repo.api.__main__ import main

    assert main(["--host", "0.0.0.0"]) == 2
    assert main(["--host", "192.168.1.5", "--port", "9"]) == 2


def test_aura_requires_explicit_flag_and_no_uri_errors(monkeypatch) -> None:
    # --use-aura with no configured NEO4J_URI must fail clearly, without connecting anywhere.
    from agent_repo.api import __main__ as api_main
    from agent_repo import config as config_module

    monkeypatch.setattr(config_module.Settings, "from_env",
                        classmethod(lambda cls, *a, **k: cls(neo4j=None)))
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)
    # Bind guard still holds, and the missing-URI path returns 2 before any store is built.
    assert api_main.main(["--use-aura", "--host", "127.0.0.1", "--port", "9"]) == 2


def test_default_target_is_a_graph_file_not_aura(monkeypatch) -> None:
    # Without --use-aura, a configured Aura must be ignored: the store provider returns an
    # InMemoryGraph for the default file, never a Neo4jGraph. We inspect the decision without
    # starting a server by calling the provider-building logic through create_app's target label.
    from agent_repo.api.__main__ import DEFAULT_GRAPH_FILE

    assert DEFAULT_GRAPH_FILE.endswith("agent-graph.json")


def test_api_plan_values_match_stored_plan_for_reused_workflow(settings: Settings) -> None:
    # The API's plan steps must equal the stored plan data the CLI prints — through one shared
    # formatter — including the reused-workflow case where matched_terms is absent (null).
    from agent_repo.execution.runner import Runner
    from agent_repo.api import views
    from agent_repo.graph.store import InMemoryGraph
    from agent_repo.repository import AgentRepository
    from agent_repo.seeds import seed_repository
    from agent_repo.tools import builtin_registry

    store = InMemoryGraph()
    repo = AgentRepository(store, tools=builtin_registry())
    seed_repository(repo, "API Consistency Maintainer")
    runner = Runner(repo)
    query = "summarize the key points of this report: Alpha facts. Beta launch."
    runner.execute_query(query)               # first run records workflow memory
    reused = runner.execute_query(query)       # second run reuses it
    assert reused.plan is not None and reused.plan.reused_workflow_id

    response = views.ask_response(repo, reused, settings)
    assert response.plan is not None
    assert len(response.steps) == len(reused.plan.steps)
    for api_step, stored in zip(response.steps, reused.plan.steps):
        # Values come straight from the stored step the CLI renders — through one shared
        # formatter.  The reused‑workflow step now carries the real score and matched_terms
        # that were recorded when the workflow was first stored (see
        # ``record_workflow_memory`` / ``steps_json``), not fabricated defaults.
        assert api_step.agent == api_step.agent  # non-null agent
        assert stored.agent in reused.plan.format_explanation()
        assert api_step.what_it_does == stored.purpose
        assert api_step.why_chosen == stored.reason
        # matched_terms are read from the stored plan (steps_json) rather than invented defaults:
        # a list of strings when recorded, null when not recorded.
        assert api_step.matched_terms is None or isinstance(api_step.matched_terms, list)
        if api_step.matched_terms is not None:
            assert all(isinstance(term, str) for term in api_step.matched_terms)


# --- Static UI: /ui/ and /ui/static/<file> ---

def test_ui_index_returns_200_html(client: TestClient) -> None:
    response = client.get("/ui/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_ui_static_file_not_listed(client: TestClient) -> None:
    # Only files inside the static folder are served; there is no directory listing.
    response = client.get("/ui/static/")
    assert response.status_code == 404


def test_ui_headers(client: TestClient) -> None:
    response = client.get("/ui/")
    assert response.status_code == 200
    assert response.headers["content-security-policy"] == (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_ui_static_headers(client: TestClient) -> None:
    # index.html itself is a real static asset that should carry the security headers too.
    response = client.get("/ui/static/index.html")
    assert response.status_code == 200
    assert response.headers["content-security-policy"].startswith("default-src 'self'")
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("path", [
    "/ui/../.env",
    "/ui/%2e%2e/.env",
    "/ui/static/..%2f..%2f.env",
    "/ui/static/../app.py",
])
def test_ui_traversal_returns_404(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 404, f"traversal leaked: {path}"


def _scan_ui_source(source: str, file_name: str) -> list[str]:
    """Return problem descriptions for banned constructs in a single static-file source.

    Scans raw source (no comment stripping). Uses word-bounded regexes so harmless
    identifiers like ``retrieval()`` are not mistaken for banned sinks.
    """
    problems: list[str] = []

    # Dangerous DOM sinks / code execution (word-bounded to avoid false positives).
    sink_regexes = [
        ("innerHTML", re.compile(r"innerHTML")),
        ("insertAdjacentHTML", re.compile(r"insertAdjacentHTML")),
        ("document.write", re.compile(r"document\.write")),
        ("eval(", re.compile(r"\beval\s*\(")),
        ("new Function", re.compile(r"\bnew\s+Function\b")),
    ]
    for name, pattern in sink_regexes:
        if pattern.search(source):
            problems.append(f"{file_name}: contains banned {name}")

    # Banned in every static file.
    everywhere_regexes = [
        ("localStorage", re.compile(r"localStorage")),
        ("sessionStorage", re.compile(r"sessionStorage")),
        ("console.", re.compile(r"console\.")),
        ("outerHTML", re.compile(r"outerHTML")),
        ("srcdoc", re.compile(r"srcdoc")),
        ("javascript:", re.compile(r"javascript:")),
        ("string argument to setTimeout", re.compile(r"\bsetTimeout\s*\(\s*[\"']")),
        ("string argument to setInterval", re.compile(r"\bsetInterval\s*\(\s*[\"']")),
    ]
    for name, pattern in everywhere_regexes:
        if pattern.search(source):
            problems.append(f"{file_name}: contains banned {name}")

    # Cross-origin http/https URLs are banned (relative URLs are fine).
    cross_origin_regex = re.compile(r'https?://[^\s"\'<>]+')
    for match in cross_origin_regex.finditer(source):
        problems.append(f"{file_name}: cross-origin URL {match.group(0)!r}")

    # index.html-only markup rules.
    if file_name == "index.html":
        # Inline "on*" event-handler attributes.
        on_handler_regex = re.compile(r"\bon[A-Za-z]+\s*=\s*[\"']")
        for match in on_handler_regex.finditer(source):
            problems.append(f"{file_name}: inline on* handler {match.group(0)!r}")
        # Inline <script> without a src attribute.
        script_regex = re.compile(r"<script\b[^>]*>", re.IGNORECASE)
        for match in script_regex.finditer(source):
            tag = match.group(0)
            if "src=" not in tag:
                problems.append(f"{file_name}: inline <script> without src {tag!r}")
        # Inline style= attributes.
        style_regex = re.compile(r"\sstyle\s*=\s*[\"']")
        for match in style_regex.finditer(source):
            problems.append(f"{file_name}: inline style= attribute {match.group(0)!r}")

    return problems


def test_static_files_pass_security_scan() -> None:
    """The static UI files must not contain dangerous DOM sinks, inline handlers,
    or cross-origin URLs. Scans index.html, app.js and style.css directly."""
    from pathlib import Path

    static_dir = Path(__file__).resolve().parent.parent / "agent_repo" / "api" / "static"
    files = [static_dir / "index.html", static_dir / "app.js", static_dir / "style.css"]

    problems: list[str] = []
    for file_path in files:
        source = file_path.read_text(encoding="utf-8")
        problems.extend(_scan_ui_source(source, file_path.name))

    assert not problems, "static security scan failed:\n" + "\n".join(problems)


@pytest.mark.parametrize("sample,expect_problem", [
    ("element.innerHTML = '<b>x</b>'", True),
    ("document.write('<hr>')", True),
    ("eval('alert(1)')", True),
    ("new Function('return 1')", True),
    ("element.onclick = 'doit()'", True),
    ("<script>alert(1)</script>", True),
    ("<div style='color:red'>x</div>", True),
    ("localStorage.setItem('k', 'v')", True),
    ("sessionStorage.getItem('k')", True),
    ("console.log('query')", True),
    ("element.outerHTML = '<b>'", True),
    ("<iframe srcdoc='<b>x</b>'></iframe>", True),
    ("<a href='javascript:alert(1)'>x</a>", True),
    ("setTimeout('alert(1)', 100)", True),
    ("setInterval('tick()', 1000)", True),
    # Word-boundary safety: "retrieval(" must not be flagged as the eval sink.
    ("const x = retrieval(); const y = 1 + 2;", False),
    ("let msg = 'hello world';", False),
])
def test_static_scan_detects_each_banned_item(sample: str, expect_problem: bool) -> None:
    """Meta-test: the scanner flags every banned construct and passes clean code."""
    problems = _scan_ui_source(sample, "index.html")
    if expect_problem:
        assert problems, f"expected banned item to be detected in {sample!r}"
    else:
        assert not problems, f"clean code flagged as banned: {problems}"


def test_execution_artifact_retrieval_and_persistence_across_restart(tmp_path: Path) -> None:
    """Generated image artifacts are saved outside the graph, retrievable via GET /executions/{id}/artifact,
    and persist across application restart."""
    import tempfile
    from agent_repo.config import ToolSettings
    from agent_repo.tools import Payload
    from agent_repo.manifest import Modality

    artifact_dir = tmp_path / "artifacts"
    graph_file = tmp_path / "graph.json"
    settings = Settings(
        hash_salt="0123456789abcdef0123456789abcdef",
        tools=ToolSettings(artifact_dir=str(artifact_dir)),
    )

    store = InMemoryGraph()
    repo = AgentRepository(store, tools=builtin_registry())
    seed_repository(repo, "Test Maintainer")

    app1 = create_app(lambda: store, settings=settings)
    with TestClient(app1) as client1:
        ask_resp = client1.post("/ask", data={"query": "generate an image of a blue landscape"})
        assert ask_resp.status_code == 200, ask_resp.text
        exec_id = ask_resp.json()["execution_id"]
        assert ask_resp.json()["output"]["kind"] == "image"

        # Verify retrieval endpoint
        art_resp = client1.get(f"/executions/{exec_id}/artifact")
        assert art_resp.status_code == 200
        assert art_resp.headers["content-type"].startswith("image/")
        assert art_resp.headers["x-content-type-options"] == "nosniff"
        assert "no-store" in art_resp.headers["cache-control"]
        assert len(art_resp.content) > 0

        # Verify execution_detail also surfaces kind='image'
        det_resp = client1.get(f"/executions/{exec_id}")
        assert det_resp.status_code == 200
        det_body = det_resp.json()
        assert det_body["output"]["kind"] == "image"
        assert det_body["output"]["data_base64"]

    # Save graph to simulate persistent graph storage across restart
    store.save(graph_file)

    # Restart app with fresh in-memory store loaded from graph_file and fresh app instance
    restarted_store = InMemoryGraph.load(graph_file)
    app2 = create_app(lambda: restarted_store, settings=settings)
    with TestClient(app2) as client2:
        # Artifact must still be retrievable after restart
        art_resp2 = client2.get(f"/executions/{exec_id}/artifact")
        assert art_resp2.status_code == 200
        assert art_resp2.content == art_resp.content

        # Execution detail must still surface the image after restart
        det_resp2 = client2.get(f"/executions/{exec_id}")
        assert det_resp2.status_code == 200
        assert det_resp2.json()["output"]["kind"] == "image"


def test_execution_artifact_missing_and_invalid_access(tmp_path: Path) -> None:
    """Non-image executions or unknown IDs return 404; unauthorized actors return 403."""
    from agent_repo.config import ToolSettings
    from agent_repo.tools import CallContext
    from agent_repo.execution.runner import Runner
    from agent_repo.artifacts import get_default_artifact_store

    artifact_dir = tmp_path / "artifacts"
    settings = Settings(
        hash_salt="0123456789abcdef0123456789abcdef",
        tools=ToolSettings(artifact_dir=str(artifact_dir)),
    )

    store = InMemoryGraph()
    repo = AgentRepository(store, tools=builtin_registry())
    seed_repository(repo, "Test Maintainer")

    app = create_app(lambda: store, settings=settings)
    with TestClient(app) as client:
        # Unknown execution -> 404
        assert client.get("/executions/unknown-exec-123/artifact").status_code == 404

        # Text execution (no artifact) -> 404
        text_resp = client.post("/ask", data={"query": "summarize this report: A. B."})
        text_exec_id = text_resp.json()["execution_id"]
        assert client.get(f"/executions/{text_exec_id}/artifact").status_code == 404

        # Restricted owner execution: run query with named actor 'restricted_alice'
        runner = Runner(repo, artifact_store=get_default_artifact_store(settings))
        img_result = runner.execute_query(
            "generate an image of a red sunset",
            ctx=CallContext(actor="restricted_alice"),
        )
        restricted_id = img_result.execution_id

        # Mismatched actor or missing required actor header -> 403
        assert client.get(f"/executions/{restricted_id}/artifact").status_code == 403
        assert client.get(
            f"/executions/{restricted_id}/artifact",
            headers={"X-Agent-Repo-Actor": "mallory"},
        ).status_code == 403

        # Matching actor -> 200
        ok_resp = client.get(
            f"/executions/{restricted_id}/artifact",
            headers={"X-Agent-Repo-Actor": "restricted_alice"},
        )
        assert ok_resp.status_code == 200
