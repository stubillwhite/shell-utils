# Constants
# ==============================================================================

COLOR_BLUE=\033[0;34m
COLOR_NONE=\033[0m

PYTHON_VERSION=python3.13

PROJECT_NAME=parquet_lookup

SENTINEL_FILE_DEPS=.make-sentinel.deps

export POETRY_VIRTUALENVS_IN_PROJECT=true

# Proxy certificates
export SSL_CERT_FILE=/etc/ssl/cert.pem

TEST_FILE=tests/parquet_lookup/test_console.py
ARGS=

# Targets
# ==============================================================================

# Help
# ======================================

help:
	@grep -E '^[0-9a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "$(COLOR_BLUE)%s|$(COLOR_NONE)%s\n", $$1, $$2}' \
		| column -t -s '|'

# Clean
# ======================================

.PHONY: stop
stop: ## Stop any running processes
	@echo 'Stopping processes'
	@poetry run dmypy stop || true

.PHONY: clean
clean: stop ## Remove all artefacts
	@echo 'Cleaning application'

.PHONY: clean-all
clean-all: clean ## Remove all artefacts and dependencies
	@echo 'Cleaning dependencies'
	@rm -rf dist/ htmlcov/
	@rm -f $(SENTINEL_FILE_DEPS)
	@poetry env remove --all
	@poetry env list --full-path | cut -f 1 -d ' ' | xargs rm -r

# Dependencies
# ======================================

$(SENTINEL_FILE_DEPS): pyproject.toml
	@echo 'Setting Python version to $(PYTHON_VERSION)'
	@poetry env use $(PYTHON_VERSION) 
	@echo 'Fetching dependencies'
	@poetry install --with dev
	@touch $(SENTINEL_FILE_DEPS)

deps: $(SENTINEL_FILE_DEPS) ## Install dependencies

.PHONY: deps-lock
deps-lock:
	@echo 'Locking poetry file'
	@poetry lock

.PHONY: deps-scan
deps-scan: deps
	@echo 'Scanning dependencies for security vulnerabilities'
	@poetry run pip-audit -r <(poetry export -f requirements.txt --with dev)

.PHONY: deps-update
deps-update: clean-all ## Update dependency versions
	@echo 'Updating dependencies'
	@poetry update

.PHONY: deps-add
deps-add: ## Add a dependency (use DEP=foo to specify the dependency)
	@poetry add $(DEP)

.PHONY: deps-remove
deps-remove: ## Remove a dependency (use DEP=foo to specify the dependency)
	@poetry remove $(DEP)

# Running
# ======================================

run: deps ## Run the app
	@export REQUESTS_CA_BUNDLE=$(SSL_CERT_FILE) \
		&& poetry run run-app $(ARGS)

.PHONY: run-build-index
run-build-index: deps ## Build one dataset's index (use ARGS='--dataset name --path path [--path path ...]')
	@poetry run run-app build-index $(ARGS)

.PHONY: run-lookup
run-lookup: deps ## Look up a key (use ARGS='--db key_index.duckdb KEY')
	@poetry run run-app lookup $(ARGS)

test-single-file: deps ## Run a single test file with coverage
	@echo 'Running test $(TEST_FILE)'
	@poetry run coverage run --source=src -m pytest $(TEST_FILE) 
	@poetry run coverage html

test: deps ## Run the tests
	@echo 'Running tests'
	@poetry run pytest --cov
	@poetry run coverage html
	@echo

check: deps ## Check the code
	@echo 'Running linter'
	@poetry run ruff check
	@echo
	@echo 'Running static type checks'
	@poetry run dmypy run -- src tests
	@echo

fix: format deps ## Fix formatting and minor style issues
	@echo 'Fixing problems'
	@poetry run ruff check --fix
	@echo

format: deps ## Format the code
	@echo 'Formatting code'
	@poetry run isort src tests
	@poetry run black src tests
	@echo

build: format test check ## Build the code
	@echo 'Building artefacts'
	@poetry build
