# Constants
# ==============================================================================

COLOR_BLUE=\033[0;34m
COLOR_NONE=\033[0m

PYTHON_VERSION?=python3.14

PROJECT_NAME=copilot_metrics

SENTINEL_FILE_DEPS=.make-sentinel.deps

export POETRY_VIRTUALENVS_IN_PROJECT=true

# Proxy certificates
export SSL_CERT_FILE=/etc/ssl/cert.pem

TEST_FILE=tests/copilot_metrics/test_console.py

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

.PHONY: run-import
run-import: deps ## Import local copilot metrics
	@poetry run copilot-metrics import

.PHONY: install-interruption-hooks
install-interruption-hooks: deps ## Install global recorder hooks (optional PROVIDER=copilot|claude|codex)
	@poetry run copilot-metrics install-interruption-hooks $(if $(PROVIDER),--provider "$(PROVIDER)")

.PHONY: run-model-report
run-model-report: deps ## Report copilot metrics grouped by model
	@poetry run copilot-metrics report --group-by model

.PHONY: run-project-report
run-project-report: deps ## Report copilot metrics by project and model
	@poetry run copilot-metrics report --group-by project

.PHONY: run-daily-report
run-daily-report: deps ## Chart combined daily input/output tokens
	@poetry run copilot-metrics report --group-by day --format chart

.PHONY: run-dashboard
run-dashboard: deps ## Open the local read-only dark dashboard
	@poetry run copilot-metrics dashboard

run: deps ## Run the app
	@echo 'Running application'
	@export REQUESTS_CA_BUNDLE=$(SSL_CERT_FILE) \
		&& poetry run run-app
	@echo

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
