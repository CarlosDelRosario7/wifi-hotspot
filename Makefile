PYTHON ?= python3
PYTHONPATH ?= src

.PHONY: help test lint format run doctor build clean

help:
	@echo "Targets:"
	@echo "  make test     Run unit tests"
	@echo "  make lint     Run ruff and shellcheck"
	@echo "  make format   Format Python sources with ruff"
	@echo "  make run      Run the CLI from the repository"
	@echo "  make doctor   Run diagnostics from the repository"
	@echo "  make build    Build a source and wheel distribution"
	@echo "  make clean    Remove caches and build artifacts"

test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest

lint:
	ruff check src tests
	ruff format --check src tests
	shellcheck scripts/*.sh wifi.sh

format:
	ruff format src tests
	ruff check --fix src tests

run:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m wifi_hotspot

doctor:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m wifi_hotspot doctor

build:
	$(PYTHON) -m build

clean:
	rm -rf build dist .pytest_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.py[co]' -delete
