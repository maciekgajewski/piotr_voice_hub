.PHONY: check lint format-check typecheck test format

check: lint format-check typecheck test

lint:
	uv run --locked ruff check .

format-check:
	uv run --locked ruff format --check .

typecheck:
	uv run --locked pyright

test:
	uv run --locked pytest

format:
	uv run --locked ruff format .
	uv run --locked ruff check --fix .
