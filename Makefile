# Constants
# ==============================================================================

COLOR_BLUE=\033[0;34m
COLOR_NONE=\033[0m

SHELL=zsh

TEST_DIR=tests

TEST_FILES=$(wildcard $(TEST_DIR)/*.bats)

# Targets
# ==============================================================================

# Help
# ======================================

.PHONY: help
help: ## Show available targets
	@grep -E '^[0-9a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "$(COLOR_BLUE)%s|$(COLOR_NONE)%s\n", $$1, $$2}' \
		| column -t -s '|'

# Clean
# ======================================

.PHONY: clean
clean: ## Remove generated artefacts
	@echo 'Cleaning generated artefacts'

# Test
# ======================================

.PHONY: test
test: ## Run all tests
	@for test_file in $(TEST_FILES); do \
		bats "$$test_file" || exit $$?; \
	done
