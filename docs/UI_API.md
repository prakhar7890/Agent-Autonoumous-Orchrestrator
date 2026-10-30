# UI API (`agent_repo/api`)

A local, read-mostly JSON HTTP service over the same graph-backed agent repository the CLI uses.
It exists so a browser client (Phase B) can read the catalog, executions, proposals and run
queries without re-implementing any core behaviour.

The service is a **thin read-only shell plus one execution endpoint (`POST /ask`)**. It does not
re-implement routing, repository, feedback, execution or privacy logic — it calls the existing
`agent_repo` modules and shared helpers (`agent_repo.api.support`, `agent_repo.api.views`), which
reuse the CLI's `_safe_text` and `_make_store`.

## Purpose

- Expose repository state as JSON for a browser client.
- Run a query through the real router/executor (`POST /ask`) and return the typed result.
- Never expose governance mutations (approve/reject/rollback), secrets, or stack traces.

## Endpoints

| Method | Path                      | Returns                                                               |
|--------|---------------------------|-----------------------------------------------------------------------|
| GET    | `/health`                 | `{status: "ok", target: "file <path>" | "Aura" | "in-memory"}` (no credentials) |
| GET    | `/agents`                 | Catalog rows (name, version, status, input->output, tools w/ risk+STUB, sub-agents, example queries) |
| GET    | `/agents/{name}`          | Detail: purpose, usefulness, I/O, pipeline, tools, versions           |
| GET    | `/agents/{name}/versions` | Lineage: SUPERSEDES chain, status, author, default marker             |
| GET    | `/executions?limit=`      | Recent executions (newest first), capped by `limit`                    |
| GET    | `/executions/{id}`        | Same content as CLI `explain` (rendered subgraph text + typed output)  |
| GET    | `/proposals`              | Proposals with status and evaluation result. **Read-only.**            |
| POST   | `/ask`                    | multipart form `query` (required) + optional `attachment` file         |

### `POST /ask`

Returns `status`, `breakpoint` (or null), `clarifying_question` (or null), `user_message`, a
`plan` header, per-step `steps`, the typed `output`, and `execution_id`, `workflow_id`,
`proposal_id`, `agent_request_id`, `steps_taken`, `tokens_used`, `elapsed_seconds`.

### Plan steps come from stored plan data — never invented

Each step is what the CLI prints, through one shared formatter (`agent_repo.api.support`):
`what_it_does`, `why_chosen` (the reason, with matched terms and score), `matched_terms`, `score`,
`how_it_helps`, `tools`, `model`. **If a value was not recorded, it is `null`** (render as "not
recorded"); the API never substitutes a default. This matters for **reused-workflow** steps, where
the stored plan records `matched_terms=[]` (→ `null`) while the `score` is genuinely stored as
`1.0`. A test compares API step values against the same stored plan the CLI renders for a reused
query.

### Typed `output`

Both `/ask` and `/executions/{id}` return `output` as an object, never a bare string:

```
{ "kind": "text" | "json" | "image", "text"?: str, "mime"?: str, "data_base64"?: str }
```

- `image` — when the goal agent's output modality is `image`, `kind="image"` with the payload's
  `mime` (the stub SVG is `image/svg+xml`) and its bytes base64-encoded in `data_base64`.
  Image bytes are **never stored in the graph**: the execution record keeps only the scrubbed
  `output_text` (`agent_repo/execution/runner.py` `_record_execution_node`). Therefore
  `/executions/{id}` can only return `kind="text"` for a stored execution; `/ask` returns
  `kind="image"` because it still holds the live `Payload`.
- **Size cap** on `data_base64` (default 2 MB, configurable): above the cap the API returns
  `kind="text"` with an explanatory message instead of the bytes. Never truncated silently.

### Image safety

Image data is returned **as base64 data only**. The API never inlines an image payload into any
HTML it serves. An SVG containing a `<script>` tag — or any markup — is opaque bytes. The
`image_generate` stub itself returns a small grey-card SVG with the prompt XML-escaped
(`html.escape`), so `<script>` and `&` in a prompt are escaped in the produced SVG; a test covers
this.

## Security rules (all tested)

- **Bind to `127.0.0.1` only.** The run entry point refuses to start if asked to bind elsewhere,
  with a clear message. No CORS origins are enabled by default.
- **The graph target is chosen at startup only**, by CLI argument, **never** from a request.
  Default is a graph file (`.\agent-graph.json`). **Aura is used ONLY with an explicit
  `--use-aura` flag** (and requires `NEO4J_URI`); without it a configured Aura is ignored, so the
  API cannot write to Aura by accident. `GET /health` reports the active target without credentials.
- **Every string in a response passes through the existing CLI scrubbing**
  (`_safe_text`: regex scrub + configured-secret redaction).
- **Errors return sanitized messages** with the same wording as the CLI, never stack traces or
  env values. Unknown ids return a clean `404` with a sanitized detail.
- **`/ask` uploads**: size limit (default 1 MB, configurable); saved to a temp file with a
  **generated** name (the client filename is never used as a path); deleted after the run.
  Oversized files are rejected with a clear `413`.
- **`/ask` calls are serialized with a lock** — the in-memory graph is single-process.
- **Request validation errors never echo the submitted value**: FastAPI's default 422 handler
  includes the raw user input in the `"input"` field of each error message. The API replaces
  this with a sanitized message naming only the field, never the submitted value, and passes
  through the same CLI scrubbing as other responses.
  *Numbers in the `limit` query‑parameter message are acceptable; free‑text submitted via
  `POST /ask` or path parameters is never echoed in any response body.*
- **Real providers stay opt‑in via env flags and off by default.** The API does not enable them.
- **No network calls** at startup or on request beyond what an opted‑in provider tool already does.

## Query limit parameter (`GET /executions?limit=`)

The `limit` query parameter is bound to the range **1 – 200** (default **25**). Requests with
`limit < 1` or `limit > 200` return a **422** response with a sanitized detail message — the
submitted value is never echoed. Within range, the value is clamped and used as the maximum
number of executions to return. Extremely large or zero values are rejected before they can be
processed.

## What the API must never do

- Never expose `approve`, `reject`, `rollback`, `feedback` or `delete`. A test asserts the **exact**
  set of routes and methods so a write endpoint cannot be added by accident.
- Never return Aura credentials, `.env` values, or any configured secret.
- Never inline untrusted bytes into HTML.
- Never accept the graph-file path or backend from a request.
- Never return a raw stack trace or a provider/model not explicitly labelled.

## How core stays importable

`agent_repo/api` is only imported when the FastAPI extra is installed. The base `agent_repo`
package imports without `fastapi`/`uvicorn`/`python-multipart`. Install with
`pip install -e ".[ui]"`.

## Run

```
python -m agent_repo.api --graph-file .\agent-graph.json
```

Fixed host `127.0.0.1`; port configurable via `--port`. Add `--use-aura` to target the configured
Aura/Neo4j backend. FastAPI's `/docs` page is acceptable for manual checking.

## Limitations

- Local only, no authentication.
- Read-only except `POST /ask` (which records the resulting execution node in the graph).
- `GET /executions/{id}` cannot reproduce image bytes (only `output_text` is stored).
