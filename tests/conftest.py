"""Repo-wide test setup."""

from dotenv import load_dotenv

# Make ANTHROPIC_API_KEY from a local, gitignored .env visible to `live` tests. Never overrides
# variables already set in the environment, and the value is never printed.
load_dotenv(override=False)

# Mock-bank fixtures (tenants on ephemeral ports, browser, context) for any test directory.
pytest_plugins = ["tests.support.bank"]
