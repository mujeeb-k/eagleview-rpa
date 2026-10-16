.PHONY: check lint typecheck test test-live bank demo-offline demo-handoff demo-full evidence

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

# Both training tenants: Lantern Bay on 8601, Copperhill on 8602. Missing passwords are generated
# into .env.
bank:
	uv run python -m mock_bank

# The offline demo: no API key, no human. Starts the bank in-process (and stops it on exit),
# replays the committed artifacts through the main scenarios, and drives the takeover and the
# approval with the scripted operator. Writes to runs/demo/, never to evidence/.
demo-offline:
	PYTHONPATH=. uv run python scripts/evidence/driver.py 04 05 06 07 08 09 10 11 12b 13 --out runs/demo

# The interactive takeover: a person uses the operator console (http://127.0.0.1:8700).
demo-handoff:
	PYTHONPATH=. uv run python scripts/demo_handoff.py

# Discover with the real model, then replay. Needs ANTHROPIC_API_KEY; spends from the ledger.
demo-full:
	PYTHONPATH=. uv run python scripts/evidence/driver.py 01 04 --out runs/demo-full

# Regenerate all evidence (real model for 01, 02, 03, 12a; 14 once approved). Each scenario is
# built in a staging folder and replaces its evidence only if it passed, so a failed discovery
# never wipes what is committed. Replays run first; a failure is reported at the end.
evidence:
	PYTHONPATH=. uv run python scripts/evidence/driver.py all; status=$$?; scripts/evidence/index.sh; exit $$status
