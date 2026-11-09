SHELL := /usr/bin/env bash
PY ?= .venv/bin/python

.PHONY: check lint test

check: lint test ## Lint and unit tests

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

test:
	$(PY) -m pytest -q
