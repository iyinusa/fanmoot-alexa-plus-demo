# fanmoot-alexa-plus-demo — development tasks.
#
# Everything runs through .venv, so no shell activation is needed and CI and a
# laptop run the identical command. Same targets as `fanmoot` and
# `fanmoot-mcp`, so muscle memory carries between the three repos.

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := uv pip install --python $(PY)
COMPOSE := docker compose -f docker-compose.yml -f docker-compose.dev.yml

# The sibling checkout the brand is copied from (HAD-11).
FANMOOT_WEB ?= ../fanmoot/web
BRAND_FILES := src/styles/tokens.css src/styles/fonts.css \
	public/fonts/lato-300.woff2 public/fonts/lato-400.woff2 \
	public/fonts/lato-700.woff2 public/fonts/lato-900.woff2 public/theme.js

.DEFAULT_GOAL := help
.PHONY: help install web-install dev dev-web test test-live evidence lint fmt types typecheck-web check build-web bundle-budget share-image brand-check brand-sync build up down logs shell clean

help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: web-install  ## Create the venv and install Python and web dependencies
	uv venv --python 3.12 $(VENV)
	$(PIP) -e ".[dev]"

web-install:  ## Install the SPA's dependencies from the lockfile
	cd web && npm ci --no-audit --no-fund

dev:  ## Run the server with reload on :8090
	$(VENV)/bin/uvicorn app.main:app --reload --port 8090

dev-web:  ## Vite dev server on :5174 (proxies /ws and /healthz to :8090)
	cd web && npm run dev

test:  ## Run the test suite (offline — no network, no built bundle needed)
	$(VENV)/bin/pytest -q

test-live:  ## W1-9 against the deployed MCP server (FMD_LIVE_MODEL=1 adds a real-model run)
	$(VENV)/bin/pytest -q -m live -s

# The chain to capture with; empty means the Bedrock rungs of FMD_MODEL_CHAIN.
CHAIN ?=
evidence:  ## W1-10: a Bedrock-only run of the standing scenario, kept in evidence/
	$(PY) scripts/capture_evidence.py $(if $(CHAIN),--chain $(CHAIN),)

lint:  ## Check style and import order
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .

fmt:  ## Autoformat and apply safe fixes
	$(VENV)/bin/ruff check --fix .
	$(VENV)/bin/ruff format .

types:  ## Type-check the server with mypy --strict
	$(VENV)/bin/mypy app scripts

typecheck-web:  ## Type-check the SPA with tsc -b
	cd web && npx tsc -b

check: lint types typecheck-web test  ## Everything CI runs — must pass before ticking a milestone task

build-web:  ## Build the SPA into app/static/web (fails if the JS is over budget, W5-3)
	cd web && npm run build

# The budget is a Vite plugin (web/budget.ts), so every build enforces it —
# this one, CI's and the Docker image's. This target is for reading the report.
bundle-budget:  ## Build the SPA and print its gzipped JS against the budget (W5-3)
	cd web && npx vite build 2>&1 | sed -n '/critical path/,/^computing/p' | grep -v '^computing'

share-image:  ## Redraw the link-preview image, web/public/share/og.png, with headless Chrome (W5-6)
	$(PY) scripts/make_share_image.py

brand-check:  ## Fail if the Floodlit tokens, fonts or theme script drifted from fanmoot
	@test -d "$(FANMOOT_WEB)" || (echo "no fanmoot checkout at $(FANMOOT_WEB) — set FANMOOT_WEB=…"; exit 1)
	@status=0; for f in $(BRAND_FILES); do \
	  cmp -s "$(FANMOOT_WEB)/$$f" "web/$$f" || { echo "drifted: web/$$f"; status=1; }; \
	done; \
	[ $$status -eq 0 ] && echo "brand matches $(FANMOOT_WEB)"; exit $$status

brand-sync:  ## Re-copy the Floodlit tokens, fonts and theme script from fanmoot
	@test -d "$(FANMOOT_WEB)" || (echo "no fanmoot checkout at $(FANMOOT_WEB) — set FANMOOT_WEB=…"; exit 1)
	@for f in $(BRAND_FILES); do cp "$(FANMOOT_WEB)/$$f" "web/$$f" && echo "copied web/$$f"; done

build:  ## Build the Docker image
	docker build -t fanmoot-alexa-plus-demo:local .

up:  ## Start the stack with the dev overlay on :8090
	@docker network inspect dokploy-network >/dev/null 2>&1 \
	  || docker network create dokploy-network
	$(COMPOSE) up --build --renew-anon-volumes

down:  ## Stop the stack
	$(COMPOSE) down

logs:  ## Tail container logs
	$(COMPOSE) logs -f demo

shell:  ## Shell into the running container
	$(COMPOSE) exec demo /bin/bash

clean:  ## Remove caches and build artefacts
	find . -type d -name __pycache__ -not -path './web/node_modules/*' -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .mypy_cache app/static/web web/tsconfig.tsbuildinfo
