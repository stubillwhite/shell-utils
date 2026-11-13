# Copilot instructions

## Project shape

This is a local-only Python CLI for historical Copilot CLI, Claude Code, and
Codex metrics. `app.py` provides `doctor`, `import`, and `report` commands:
discovery and CLI validation happen there, while `doctor` does not parse
transcripts or create a database.

Provider JSONL parsing and normalization live in `collectors/`. Collectors emit
the immutable `Metric`, `Diagnostic`, and `Collection` records defined in
`models.py`. `storage.py` hashes identities, imports normalized records into
DuckDB, replaces changed source data transactionally, and exposes deduplicated
events. `reports.py` applies date/timezone and grouping rules to those events
and renders terminal, JSON, or CSV output. Keep provider-specific parsing in
collectors rather than adding it to the CLI or reporting layer.

## Build, test, and lint

The project requires Python 3.12+ and uses Poetry. Install dependencies with
`poetry install`.

```sh
poetry run pytest --cov
poetry run pytest tests/copilot_metrics/test_collectors.py::test_copilot_usage_deduplicates_api_calls_without_counting_checkpoint_totals
poetry run isort --check-only src tests
poetry run black --check src tests
poetry run ruff check
poetry run mypy src tests
poetry build
```

CI runs `make test`. Make targets also include `make check`, `make format`,
`make run-import`, `make run-model-report`, `make run-project-report`, and
`make run-daily-report`. The single-test example uses pytest’s
`file.py::test_name` selector; substitute the relevant test path and function.

## Data and implementation conventions

- Treat provider log formats as unstable local evidence, not vendor APIs. Parse
  only explicitly supported structured fields; do not mine free text.
- Persist only normalized/allowlisted metrics and diagnostics. Do not retain
  prompts, responses, tool arguments/results, commands, code, or full source
  paths. Hash session/source/project identities; project labels are directory
  basenames.
- Deduplicate repeated records using provider/session/event identity and kind.
  Keep provider usage categories separate; missing/unobserved values stay null,
  not zero. Table/JSON/CSV remain provider-separated. The daily chart alone
  combines reported input and output counts across providers separately. Its
  stacked bars use input + output for geometry only, not a stored/exported
  total. Never add cache/reasoning categories to those counts.
- Imports should remain repeatable: changed files replace prior normalized
  records in a transaction, unchanged files are skipped, and parser behavior
  changes should update `COLLECTOR_REVISION` so existing sources are rescanned.
- Surface malformed or unsupported input as diagnostic codes without including
  raw record content. Preserve valid records from the same file where possible.
- Keep date handling timezone-aware: imported timestamps are normalized to UTC,
  report date bounds are inclusive calendar dates, and daily grouping uses the
  selected report timezone.
- Tests use synthetic JSONL fixtures. Add provider/schema behavior tests in
  `tests/copilot_metrics/test_collectors.py`; CLI and persistence/report
  behavior have dedicated test modules under `tests/`.
