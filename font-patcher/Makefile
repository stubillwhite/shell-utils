# Constants
# ==============================================================================

COLOR_BLUE=\033[0;34m
COLOR_NONE=\033[0m

UNPATCHED_DIR=unpatched
PATCHED_DIR=patched

SYSTEM_FONT_DIR=/System/Library/Fonts

FONTS=\
	  Monaco.ttf

UNPATCHED_FONTS=$(addprefix $(UNPATCHED_DIR)/, $(FONTS))

CELLAR_FONT_DIRS=$(shell find /opt/homebrew/Caskroom -name '*.ttf' | xargs dirname | sort | uniq | tr '\n' ':')
VPATH=$(SYSTEM_FONT_DIR):$(CELLAR_FONT_DIRS)

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

.PHONY: clean
clean: ## Remove all artefacts
	@echo 'Cleaning application'
	@rm -rf \
		$(UNPATCHED_DIR) $(PATCHED_DIR)

.PHONY: clean-all
clean-all: clean ## Remove all artefacts and dependencies
	@echo 'Cleaning dependencies'

# Patching
# ======================================

$(UNPATCHED_DIR)/%: %
	@mkdir -p $(UNPATCHED_DIR)
	@echo "Copying $@"
	@cp $< $@

.PHONY: unpatched
unpatched: $(UNPATCHED_FONTS)

patched: unpatched
	@echo "Starting Docker..."
	@docker desktop start
	@echo
	@echo "Patching fonts..."
	@docker run --rm -v ./$(UNPATCHED_DIR):/in -v ./$(PATCHED_DIR):/out  -e "PN=1" nerdfonts/patcher -c --mono
	# @docker run --rm -v ./$(UNPATCHED_DIR):/in -v ./$(PATCHED_DIR):/out  -e "PN=1" nerdfonts/patcher -c
	@echo
	@echo "Stopping Docker..."
	@docker desktop stop

patch-fonts: patched ## Patch fonts
	@open $(PATCHED_DIR)

