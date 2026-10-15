.PHONY: check lint typecheck test test-live bank demo-offline demo-full

check: lint typecheck test

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run pyright

test:
	uv run pytest

# Tests that call the real Claude API. Reads ANTHROPIC_API_KEY from the environment or .env.
test-live:
	uv run pytest -m live -v

bank:
	@echo "make bank: not implemented until P1 (mock bank)" >&2; exit 1

demo-offline:
	@echo "make demo-offline: not implemented until P11" >&2; exit 1

demo-full:
	@echo "make demo-full: not implemented until P11" >&2; exit 1
