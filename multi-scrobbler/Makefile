# vim:fdm=marker

# Constants                                                                 {{{1
# ==============================================================================

COLOR_BLUE=\033[0;34m
COLOR_NONE=\033[0m

SENTINEL_FILE_DEPS=.make-sentinel.deps

# Targets                                                                   {{{1
# ==============================================================================

# Help                              {{{2
# ======================================

help:
	@grep -E '^[0-9a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "$(COLOR_BLUE)%s|$(COLOR_NONE)%s\n", $$1, $$2}' \
		| column -t -s '|'

# Clean                             {{{2
# ======================================

.PHONY: clean
clean: ## Remove all artefacts
	@echo 'Cleaning application'

.PHONY: clean-all
clean-all: clean ## Remove all artefacts and dependencies
	@echo 'Cleaning dependencies'
	@rm -rf multi-scrobbler
	@rm -f $(SENTINEL_FILE_DEPS)

# Dependencies                      {{{2
# ======================================

$(SENTINEL_FILE_DEPS):
	@echo 'Fetching dependencies'
	@git clone git@github.com:FoxxMD/multi-scrobbler.git
	@pushd multi-scrobbler \
		&& npm install \
		&& npm run docs:install \
		&& npm run build
	@touch .make-sentinel.deps

deps: $(SENTINEL_FILE_DEPS) ## Install dependencies

run: deps ## Run multi-scrobbler
	@echo 'Running multi-scrobbler'
	@ pushd multi-scrobbler \
		&& export SPOTIFY_CLIENT_ID=${SECRET_SPOTIFY_CLIENT_ID} \
		&& export SPOTIFY_CLIENT_SECRET=${SECRET_SPOTIFY_CLIENT_SECRET} \
		&& export SPOTIFY_REDIRECT_URI=http://localhost:9078/callback \
		&& export LASTFM_API_KEY=${SECRET_LASTFM_API_KEY} \
		&& export LASTFM_SECRET=${SECRET_LASTFM_SECRET} \
		&& npm run start
