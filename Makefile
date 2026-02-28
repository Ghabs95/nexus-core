SHELL := /bin/sh

VENV_BIN ?= ./venv/bin
PYTEST ?= $(VENV_BIN)/pytest
RUFF ?= $(VENV_BIN)/ruff
MYPY ?= $(VENV_BIN)/mypy

FAST_PYTEST_ADDOPTS ?= -q -x --disable-warnings
EXAMPLE_SRC ?= examples/telegram-bot/src

.PHONY: help lint-file type-file test-file test-example-file test-changed-core \
	test-changed-telegram premerge-core premerge-telegram premerge-all

help:
	@echo "Low-token local workflow targets:"
	@echo "  make lint-file FILE=path/to/file.py"
	@echo "  make type-file FILE=path/to/file.py"
	@echo "  make test-file TEST=tests/test_module.py"
	@echo "  make test-example-file TEST=examples/telegram-bot/tests/test_module.py"
	@echo "  make test-changed-core"
	@echo "  make test-changed-telegram"
	@echo "  make premerge-core"
	@echo "  make premerge-telegram"
	@echo "  make premerge-all"

lint-file:
	@test -n "$(FILE)" || (echo "Usage: make lint-file FILE=path/to/file.py" && exit 2)
	@$(RUFF) check "$(FILE)"

type-file:
	@test -n "$(FILE)" || (echo "Usage: make type-file FILE=path/to/file.py" && exit 2)
	@$(MYPY) --follow-imports=skip --ignore-missing-imports --hide-error-context --no-error-summary "$(FILE)"

test-file:
	@test -n "$(TEST)" || (echo "Usage: make test-file TEST=tests/test_module.py" && exit 2)
	@$(PYTEST) -o addopts='$(FAST_PYTEST_ADDOPTS)' "$(TEST)"

test-example-file:
	@test -n "$(TEST)" || (echo "Usage: make test-example-file TEST=examples/telegram-bot/tests/test_module.py" && exit 2)
	@PYTHONPATH="$(EXAMPLE_SRC)" $(PYTEST) -o addopts='$(FAST_PYTEST_ADDOPTS)' "$(TEST)"

test-changed-core:
	@set -eu; \
	TESTS="$$( \
		{ git diff --name-only --diff-filter=ACMR; git diff --name-only --diff-filter=ACMR --cached; } \
		| grep -E '^tests/test_.*\.py$$' \
		| sort -u \
	)"; \
	if [ -z "$$TESTS" ]; then \
		echo "No changed core test files detected."; \
		exit 0; \
	fi; \
	for t in $$TESTS; do \
		echo "==> $$t"; \
		$(PYTEST) -o addopts='$(FAST_PYTEST_ADDOPTS)' "$$t"; \
	done

test-changed-telegram:
	@set -eu; \
	TESTS="$$( \
		{ git diff --name-only --diff-filter=ACMR; git diff --name-only --diff-filter=ACMR --cached; } \
		| grep -E '^examples/telegram-bot/tests/test_.*\.py$$' \
		| sort -u \
	)"; \
	if [ -z "$$TESTS" ]; then \
		echo "No changed Telegram example test files detected."; \
		exit 0; \
	fi; \
	for t in $$TESTS; do \
		echo "==> $$t"; \
		PYTHONPATH="$(EXAMPLE_SRC)" $(PYTEST) -o addopts='$(FAST_PYTEST_ADDOPTS)' "$$t"; \
	done

premerge-core:
	@$(PYTEST) -o addopts='$(FAST_PYTEST_ADDOPTS)' tests

premerge-telegram:
	@PYTHONPATH="$(EXAMPLE_SRC)" $(PYTEST) -o addopts='$(FAST_PYTEST_ADDOPTS)' examples/telegram-bot/tests

premerge-all:
	@$(MAKE) premerge-core
	@$(MAKE) premerge-telegram
