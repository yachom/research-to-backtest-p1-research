.PHONY: install test lint fmt typecheck check

VENV := .venv
PY := $(VENV)/bin/python

install:
	python3 -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check src tests
	$(PY) -m ruff format --check src tests

fmt:
	$(PY) -m ruff check --fix src tests
	$(PY) -m ruff format src tests

typecheck:
	$(PY) -m mypy

check: lint typecheck test
