SHELL := /usr/bin/env bash
PY ?= .venv/bin/python

.PHONY: check lint test

check: lint test ## Lint and unit tests

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

test:
	$(PY) -m pytest -q

.PHONY: e2e-up e2e-down rbac e2e

e2e-up: ## kind + CNPG + test cluster (credentials generated, never committed)
	hack/e2e-up.sh

e2e-down:
	kind delete cluster --name pg-ops

rbac: ## The agent's service account can read and cannot write
	e2e/rbac.sh

e2e: rbac ## Tools against the kind cluster, over MCP, as the agent
	$(PY) -m pytest -q e2e -p no:cacheprovider
