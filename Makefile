.PHONY: lint test check install-dev

lint:
	ruff check .

test:
	pytest

check: lint test

install-dev:
	pip install -e '.[dev]'
