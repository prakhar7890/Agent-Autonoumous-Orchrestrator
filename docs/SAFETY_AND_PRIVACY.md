# Safety and Privacy

## Tool risk tiers and authority grants

Every `Tool` has a risk tier: `low`, `medium` or `high`. The tier is part of the tool's registry
spec, not of any prompt.

- **HIGH-risk tools require an explicit authority grant in the call context, enforced in code.**
  `ToolRegistry.call()` checks `_authorised()` before running anything: a HIGH-risk tool is allowed
  only if the context carries an `AuthorityGrant` for that exact tool whose `granted_by` is a
  **named human** and whose `reason` is non-empty. Otherwise it raises `PermissionDeniedError`, which
  is a **hard-stop** breakpoint (`permission_denied`).
- `email_send` is the seeded HIGH-risk tool (a labelled STUB that never sends). The demo shows the
  denial and then the grant path.
- **Audit log.** `ToolRegistry` records an `AuditEntry` (`at`, `tool`, `risk`, scrubbed `actor`,
  `outcome` ∈ `ok`/`denied`/`error`, scrubbed `detail`) for every call, denial and error.
- **Transitive guard.** A prerequisite or a sub-agent route that can reach a HIGH-risk tool is
  excluded from automatic chain insertion, and a non-human author cannot add such a tool directly or
  through a sub-agent.

## Privacy: scrubbing and hashing

`agent_repo/privacy.py` provides `scrub()` / `scrub_obj()` and `UserIdHasher`.

Scrubbed before storage (queries, plans, traces, outputs, comments, audit entries, manifest fields):

- key/value secrets (`password: …`, `api_key=…`, `token = …`), `Bearer …` tokens;
- well-known API key shapes (OpenAI `sk-…`, Groq `gsk_…`, Google `AIza…`, AWS `AKIA…`, GitHub
  `gh[pousr]_…`, Slack `xox…-…`);
- emails, Luhn-checked payment cards (13–19 digits), Aadhaar (12 digits, first 2–9), PAN
  (`AAAAA9999A`), and phone-like numbers.
- Values stored under sensitive-looking keys (`password`, `api_key`, `*_token`, …) are fully
  redacted by `scrub_obj()`.

**End-user identifiers are only ever stored as salted HMAC-SHA256 hashes** (`u_<32 hex>`); the salt
(`AGENT_REPO_HASH_SALT`, ≥ 16 chars) is required and never stored with the data.

**Developer / approver names are stored in plain text, on purpose.** Author, approver and
agent-owner identities (`Agent.created_by`, `AgentVersion.author`, the `stable_by` /
`pending_approval_by` transition properties, and `AuditEntry.actor`) are operator identities kept
for audit and governance, not end-user PII, so they are **not** hashed. Only end-user feedback ids
are hashed.

### Honest limitations of the scrubber

- Scrubbing is **regex-based and best-effort**. It **does not catch personal names, addresses, or
  free-form personal data.** This is a documented limitation, not a guarantee.
- Because the phone rule is a digit-pattern rule, it may also redact some **phone-like numbers that
  appear in ordinary free text** (a false positive). Treat redaction as lossy.
- Digit-based rules require non-word boundaries on both sides so they do not damage hex ids or
  content hashes.

### Where data is protected, and where it is not

- Free text (query, comment, reason, manifest fields) is **scrubbed at write time**, i.e. before it
  is stored in the graph.
- The **Cypher export** redacts the *exact configured credential values* only, but otherwise
  **preserves stored identifiers and content hashes** so the export stays a faithful, reloadable
  copy of the graph. A stored SHA-256 hash is not a secret; it is included deliberately.
- The same scrub of free text applies to manifests: a manifest containing secret- or PII-like
  content is refused at creation.

## Governance: honest statement

**"Human approver" is a naming convention, not authentication.** `is_human_name()` rejects empty
names and names beginning with `llm` or `system` (case-insensitive). It separates machine authors
from human reviewers in code; it does **not** verify a person's identity. Do not describe it as
access control.

## No destructive operations

`GraphStore` has **no delete method**; "delete" is the `deprecated` status, so old versions stay
stored and traceable. `Neo4jGraph` exposes **no public wipe, delete or arbitrary-query method**:
its public surface is the same typed `GraphStore` API as `InMemoryGraph`.

Qualification: `Neo4jGraph` does keep **private** helpers that run parameterised Cypher —
`_read()` (`agent_repo/graph/neo4j_store.py:154`) and `_write()` (`agent_repo/graph/neo4j_store.py:160`).
Neither is part of the public interface, but code with access to the instance could call `_write`
with destructive Cypher. The "no delete" guarantee is therefore an **API/design** guarantee, not a
hard database-level one. Actual test wiping exists only in `tests/conftest.py`, is confined to the
private helper, and is gated behind the shell-only `NEO4J_TEST_WIPE=1` flag, which is never read
from `.env` and never set by the application.

## Data flow to external providers

By default (`MockLLM`, `AGENT_REPO_TRANSCRIBER=stub`, `AGENT_REPO_IMAGE_CLASSIFIER=stub`, and
`--graph-file` / `InMemoryGraph`), **no request data leaves the local process**. External network
calls happen only when explicitly configured:

| Path | How it is enabled | What leaves the process | Pre-transmission scrubbing / limits |
|---|---|---|---|
| **Graph store (Neo4j AuraDB)** | `NEO4J_URI` set (and `--graph-file` not passed) | Node and relationship properties over TLS Bolt (`neo4j+s://`) | All free-text fields (queries, plans, traces, outputs, feedback comments, audit entries, manifests) are scrubbed via `scrub()` / `scrub_obj()` **before** graph writes; end-user IDs are salted HMAC-SHA256 hashes (`u_<32 hex>`). |
| **LLM candidate rewrite** (`FeedbackLoop.generate_candidate`) | `proposals generate <id> --llm <provider:model>` (`gemini`, `groq`, `openai`) | Target `AgentVersion`'s canonical manifest JSON (`base.manifest.canonical_json()`) plus fixed rewrite instructions (`agent_repo/feedback.py:318-330`) | Manifest fields are scrubbed when created. End-user queries, feedback comments, and user hashes are **not** included in the rewrite prompt. |
| **LLM unmatched-query draft** (`LangChainLLM.draft_agent`) | `Runner(..., llm=make_llm("<provider:model>"))` on a `no_match` breakpoint | Scrubbed query text (`scrub(query)`) and `payload_modality.value` (`agent_repo/llm.py:180-188`) | Regex-scrubbed before `generate()` is called; subject to the regex scrubber's documented limitations (personal names and free-form PII are not caught). |
| **Real speech-to-text** (`audio_transcribe_groq`) | `AGENT_REPO_TRANSCRIBER=groq` | Attached audio file's raw bytes (`path.open("rb")`) and filename (`path.name`) sent to Groq ASR (`whisper-large-v3`, `agent_repo/tools.py:297-298`) | **Binary audio cannot be scrubbed before upload** — spoken PII or secrets in the recording are transmitted verbatim to Groq. The returned transcript text is scrubbed before it is written to the execution trace or graph. |
| **Real image classification** (`image_classify_gemini`) | `AGENT_REPO_IMAGE_CLASSIFIER=gemini` | Attached image's raw bytes (`path.read_bytes()`), guessed MIME type, and the fixed `_CLASSIFY_INSTRUCTION` prompt sent to Gemini (`gemini-3.5-flash`, `agent_repo/tools.py:342-347`) | **Raw image pixels cannot be scrubbed before upload** — visible text, faces, or sensitive content in the image are transmitted verbatim to Google Gemini. The returned JSON labels are parsed and scrubbed before storage. |

**Credential handling for external calls:** API keys (`GOOGLE_API_KEY`, `GROQ_API_KEY`,
`OPENAI_API_KEY`, `NEO4J_PASSWORD`) are read in-process from `.env` / `os.environ` and passed only
to the provider SDK client constructors. Missing-key, configuration, and parser errors name only the
setting or field (`tests/test_error_messages.py`), never the value.

