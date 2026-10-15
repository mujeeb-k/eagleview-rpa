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

# Both training tenants: Lantern Bay on 8601, Copperhill on 8602. Needs IRONBRIDGE_TELLER_PASSWORD.
bank:
	uv run python -m mock_bank

demo-offline:
	@echo "make demo-offline: not implemented until P11" >&2; exit 1

demo-full:
	@echo "make demo-full: not implemented until P11" >&2; exit 1
