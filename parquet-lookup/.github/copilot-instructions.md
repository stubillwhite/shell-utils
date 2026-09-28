# Copilot instructions for parquet_lookup

## Build, test, and lint

Use the project’s Makefile targets rather than ad hoc commands.

- Install dependencies: `make deps`
- Build or replace one dataset index: `make run-build-index ARGS='--dataset name --path s3://bucket/path --path ./local/path --db key_index.duckdb'`. Repeat `--path` to combine multiple local/S3 roots. Use `--key-column id --content-column xml` for differently named fields. Progress is logged to stderr.
- Look up a key: `make run-lookup ARGS='--db key_index.duckdb KEY'`
- Run either CLI subcommand directly: `make run ARGS='build-index|lookup ...'`
- Run the full test suite: `make test`
- Run a single test file: `make TEST_FILE=tests/parquet_lookup/test_app.py test-single-file`
- Run a single pytest node or expression: `poetry run pytest tests/parquet_lookup/test_app.py -k test_name`
- Run lint and type checks: `make check`
- Auto-format code: `make format`
- Autofix lint issues where safe: `make fix`
- Build the package: `make build`

The repo is configured for Poetry and the Makefile sets `POETRY_VIRTUALENVS_IN_PROJECT=true`, so commands should be run inside the repo-local virtualenv.

## High-level architecture

This repo is a small Python CLI for building and querying a local DuckDB index over Parquet datasets in S3 or on disk.

- Package code lives under `src/parquet_lookup/`.
- `src/parquet_lookup/__main__.py` is the CLI entry point used by the Poetry script `run-app`.
- `src/parquet_lookup/app.py` contains the `build-index` and `lookup` command handlers. Index builds use PyArrow to discover Parquet files and read only the configured key column, then store key-to-file/row-group locations and column mapping in DuckDB. Lookups query DuckDB first and use PyArrow for one targeted row-group read of the configured key and content columns.
- Tests live under `tests/`, with package-specific tests in `tests/parquet_lookup/` and a minimal console smoke check in `tests/test_console.py`.

There is no framework or layered architecture. Keep the IO boundaries testable through the existing filesystem parameters and use temporary local Parquet files and DuckDB databases in tests.

## Key conventions

- Use the repo’s existing Poetry + Makefile workflow rather than inventing separate tooling.
- Keep code under `src/` and avoid creating new top-level modules unless the task clearly requires it.
- This project uses Black and Ruff as the default formatting and linting tools.
- Black line length is set to 120 in `pyproject.toml`.
- Ruff is scoped to `src` and intentionally excludes `tests`, with single-line import grouping enabled via `isort`.
- Type checking is done with `dmypy` across `src` and `tests`.
- A build accepts exactly one `--dataset NAME` and one or more `--path PATH` values (local paths and S3 URIs may be mixed); it replaces that named dataset across all supplied paths. Other datasets remain intact and failures must preserve the previous index.
- Parquet column names default to `key` and `xml_content`; `--key-column` and `--content-column` override them per dataset and are persisted for lookups.
- AWS credentials use PyArrow’s standard credential resolution; do not add credentials to project configuration.
