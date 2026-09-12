# Paperstand — developer entry points.
#
# The backend runs through uv (see backend/pyproject.toml), the frontend through
# npm (see frontend/package.json). Everything here works from the repo root.

SHELL := /bin/bash
.DEFAULT_GOAL := help

UV ?= uv
BACKEND := $(UV) run --project backend
RUFF_CONFIG := backend/pyproject.toml

# Local development defaults; override on the command line or in .env.
OUT ?= ./library
LIBRARY_PATH ?= ./library
DATA_PATH ?= ./data
API_PORT ?= 8000
PORT ?= 8080

.PHONY: help dev dev-api dev-web test test-api test-web lint lint-api lint-web \
	fmt gen-api sample-library docker-build docker-run

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-16s\033[0m %s\n", $$1, $$2}'

dev: ## Run the API and the frontend dev server together
	@$(MAKE) -j2 dev-api dev-web

# `--no-proxy-headers` is not optional. Uvicorn reads `X-Forwarded-*` by default,
# and its middleware runs *outside* the application: it rewrites the client
# address from `X-Forwarded-For` before Paperstand's own middleware can decide
# whether the request came from a trusted proxy, which then refuses to apply
# `X-Forwarded-Host` and leaves the OPDS feed full of backend URLs. The
# application does the whole job itself, and `paperstand serve` runs it that way.
dev-api: ## Run the API with autoreload on $(API_PORT)
	PAPERSTAND_LIBRARY="$(LIBRARY_PATH)" PAPERSTAND_DATA="$(DATA_PATH)" \
		$(BACKEND) uvicorn paperstand.main:app --reload --no-proxy-headers \
		--port $(API_PORT)

dev-web: ## Run the SvelteKit dev server (proxies /api to $(API_PORT))
	cd frontend && npm run dev

test: test-api test-web ## Run every test suite

test-api: ## Run the backend test suite
	# `cd` first, not `$(BACKEND)`: pytest reads backend/pyproject.toml (its
	# parallelism, its markers) by searching upward from where it starts, and
	# `uv run --project` alone leaves it starting from the repository root.
	cd backend && $(UV) run pytest

test-web: ## Run the frontend test suite
	cd frontend && npm test

lint: lint-api lint-web ## Lint and type-check everything

lint-api: ## ruff + mypy on the backend and the scripts
	$(BACKEND) ruff check --config $(RUFF_CONFIG) backend scripts
	$(BACKEND) ruff format --check --config $(RUFF_CONFIG) backend scripts
	cd backend && $(UV) run mypy

lint-web: ## prettier + eslint + svelte-check on the frontend
	cd frontend && npm run lint && npm run check

fmt: ## Format everything in place
	$(BACKEND) ruff format --config $(RUFF_CONFIG) backend scripts
	$(BACKEND) ruff check --fix --config $(RUFF_CONFIG) backend scripts
	cd frontend && npm run format

# Both outputs are committed, and CI regenerates them and fails on a diff, so
# this has to be run after any change to the API's request or response models.
gen-api: ## Export openapi.json and regenerate the typed API client
	$(BACKEND) python scripts/export_openapi.py
	cd frontend && npm run gen:api

sample-library: ## Generate a fake library in $(OUT)
	$(BACKEND) python scripts/make_sample_library.py "$(OUT)"

docker-build: ## Build the container image
	docker build -t paperstand:dev .

# `--mount` wants absolute paths and, unlike `-v`, refuses to invent a missing
# source — hence the `mkdir -p`. The paths are resolved by the shell rather than
# by `$(abspath ...)`, which splits on spaces.
docker-run: ## Run the container image on $(PORT)
	@mkdir -p "$(LIBRARY_PATH)" "$(DATA_PATH)"
	docker run --rm -it \
		-p $(PORT):8080 \
		-e PUID=$${PUID:-1000} -e PGID=$${PGID:-1000} -e TZ=$${TZ:-Europe/Rome} \
		--mount "type=bind,src=$$(cd "$(LIBRARY_PATH)" && pwd),dst=/library,ro" \
		--mount "type=bind,src=$$(cd "$(DATA_PATH)" && pwd),dst=/data" \
		paperstand:dev
