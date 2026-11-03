# Cleanup log

## Audit and authorization

The workspace and Git status were checked on 2026-11-03. The user explicitly authorized deleting
the confirmed disposable generated paths listed below for this milestone. Only those exact paths
were removed. Each was confirmed to be an untracked/ignored directory, not a reparse point, and
reproducible.

## Removed generated files and directories

| Path | Why disposable | Regeneration |
|---|---|---|
| `.pytest_cache/` | Pytest cache only | Recreated by `python -m pytest` |
| `.ruff_cache/` | Ruff cache only | Recreated by Ruff |
| `build/` | Reproducible package build output | Recreated by the configured build backend |
| `agent_repo.egg-info/` | Generated editable-install metadata | Recreated by the documented editable install |
| `agent_repo/__pycache__/` | Python bytecode | Recreated by importing the package |
| `agent_repo/api/__pycache__/` | Python bytecode | Recreated by importing the API |
| `agent_repo/execution/__pycache__/` | Python bytecode | Recreated by importing execution modules |
| `agent_repo/graph/__pycache__/` | Python bytecode | Recreated by importing graph modules |
| `tests/__pycache__/` | Python bytecode | Recreated by running tests |

Two Playwright run-output directories under `%TEMP%` were also removed after their contents were
classified as generated test traces. The failed-run issue was understood and fixed before its
trace was deleted; the final passing run's traces were immediately removed as reproducible:

- `C:\Users\PRAKHA~1\AppData\Local\Temp\agent-repo-playwright-milestone-3d00ea8a48b94670bcd205b0e939fdc7`
  — failed-run screenshot, error context, and traces for the resolved non-unique history locator.
- `C:\Users\PRAKHA~1\AppData\Local\Temp\agent-repo-playwright-final-0d885e3c950941c6baf6c25433cbfda0`
  — passing-run traces, deleted after recording the 14/14 result.

Two temporary JUnit XML summaries were removed after extracting the actual per-file test counts:

- `C:\Users\PRAKHA~1\AppData\Local\Temp\agent-repo-pytest-final-ec3c022d39304b01a88ea493ca1ff519.xml`
- `C:\Users\PRAKHA~1\AppData\Local\Temp\agent-repo-pytest-final-2b8221b7b893477d82660345868cc8ff.xml`

They are reproducible by rerunning the documented offline pytest suite with `--junitxml=<temp
path>`. The first parse attempt failed due to a PowerShell command-quoting issue; its XML contained
only test names/outcomes and was discarded, then the suite was rerun and counts extracted
successfully.

## Retained reports and uncertain candidates

- Existing `playwright-report/` and `test-results/` were preserved.
- `agent_repo_artifacts/`, `playwright-fixture.json`, `local-ollama-demo.json`,
  `local-ollama-demo2.json`, and `Agent_Repository_Final_Project_Report.docx` remain because
  their roles as user output, fixtures, or historical evidence cannot safely be inferred from
  their names.
- `graphify-out/` remains as tracked project evidence/output.
- `.venv/` and `node_modules/` remain as existing environments/dependencies.
- Git status already showed deletions of
  `agent-repository-2026-10-30-clean.zip`,
  `agent-repository-2026-10-30-clean-2.zip`, and
  `agent-repository-2026-10-30-clean-3.zip`; those pre-existing changes were not altered or
  attributed to this cleanup.
- Existing `.gitignore` rules cover bytecode, pytest caches, build/dist output, Playwright
  reports, and test results; `.ruff_cache/` is also ignored.

No source, tests, credentials, user/application data, historical records, backups, migrations, or
Neo4j data were removed or modified as part of cleanup. No Neo4j query or write was performed.

## Current cleanup milestone — 2026-11-03

The user was shown the following candidate list before deletion. Immediately before removal, this
PowerShell preflight confirmed that all ten exact paths existed, were ignored by Git, were
untracked, and were not reparse points:

```powershell
$targets = @('.pytest_cache','.ruff_cache','agent_repo\__pycache__','agent_repo\api\__pycache__','agent_repo\execution\__pycache__','agent_repo\graph\__pycache__','tests\__pycache__','playwright-report','playwright-report-ollama','test-results')
foreach ($path in $targets) { if (-not (Test-Path -LiteralPath $path)) { throw "Candidate disappeared before cleanup: $path" }; $item = Get-Item -LiteralPath $path -Force; if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing to delete reparse point: $path" }; git check-ignore -q -- $path; if ($LASTEXITCODE -ne 0) { throw "Refusing to delete non-ignored path: $path" }; $tracked = @(git ls-files -- $path); if ($tracked.Count -gt 0) { throw "Refusing to delete tracked path: $path" } }; 'Pre-delete checks passed for all 10 exact candidates.'
```

Result: `Pre-delete checks passed for all 10 exact candidates.`

The explicitly authorized directories were then removed and checked absent with:

```powershell
$targets = @('.pytest_cache','.ruff_cache','agent_repo\__pycache__','agent_repo\api\__pycache__','agent_repo\execution\__pycache__','agent_repo\graph\__pycache__','tests\__pycache__','playwright-report','playwright-report-ollama','test-results'); foreach ($path in $targets) { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop }; foreach ($path in $targets) { if (Test-Path -LiteralPath $path) { throw "Cleanup verification failed; path remains: $path" } }; 'Removed and verified absent: ' + ($targets -join ', ')
```

Result:

```text
Removed and verified absent: .pytest_cache, .ruff_cache, agent_repo\__pycache__, agent_repo\api\__pycache__, agent_repo\execution\__pycache__, agent_repo\graph\__pycache__, tests\__pycache__, playwright-report, playwright-report-ollama, test-results
```

| Removed path | Why disposable | Regeneration |
|---|---|---|
| `.pytest_cache/` | Pytest-generated cache; ignored and untracked | Recreated by `python -m pytest` |
| `.ruff_cache/` | Ruff-generated cache; ignored and untracked | Recreated by Ruff |
| `agent_repo/__pycache__/` | Python bytecode cache; ignored and untracked | Recreated by importing package modules |
| `agent_repo/api/__pycache__/` | Python bytecode cache; ignored and untracked | Recreated by importing API modules |
| `agent_repo/execution/__pycache__/` | Python bytecode cache; ignored and untracked | Recreated by importing execution modules |
| `agent_repo/graph/__pycache__/` | Python bytecode cache; ignored and untracked | Recreated by importing graph modules |
| `tests/__pycache__/` | Python bytecode cache; ignored and untracked | Recreated by running tests |
| `playwright-report/` | Generated Playwright report output; ignored and untracked | Recreated by a Playwright run configured to emit an HTML report |
| `playwright-report-ollama/` | Generated historical Playwright report output; ignored and untracked | Recreated by its corresponding historical Playwright run |
| `test-results/` | Generated Playwright result/trace output from the passing deterministic run; ignored and untracked | Recreated by `npx --no-install playwright test --config=playwright.config.js --workers=1 --reporter=list` |

These are the only paths removed in this cleanup milestone. The previously documented removals
above are historical cleanup actions, not additional removals in this milestone. The older
statement that Playwright reports/results were retained described the earlier cleanup point;
the exact report/result directories removed now are listed above.

The existing `.gitignore` already contains patterns for all these cache/report directories; no
ignore rule was added or changed for this cleanup. No uncertain candidates were removed. The
other files listed under “Retained reports and uncertain candidates,” including `.venv/`,
`node_modules/`, user artifacts, historical JSON fixtures, and Neo4j-related evidence, remain.
No tests, provider calls, Neo4j queries/writes, or Neo4j volume operations were run for this
documentation/cleanup-only milestone.
