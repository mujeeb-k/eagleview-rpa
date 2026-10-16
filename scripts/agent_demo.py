"""Ask a question that an AI agent answers through the Eagleview catalog.

    uv run python scripts/agent_demo.py "What's in member 10482's savings?"

Claude (plain tool use, no computer use) picks a catalog tool; the capability replays with no
LLM; Claude answers from the result. Needs ANTHROPIC_API_KEY, the mock bank (`make bank`), and at
least one approved capability (`eagleview approve`). Spend goes to runs/discovery_ledger.jsonl."""

import asyncio
import sys
from pathlib import Path

import httpx2

from eagleview.artifacts.loader import app_profile_path, tenant_overlays, tenant_path
from eagleview.artifacts.yaml_io import load_app_profile, load_policy, load_tenant
from eagleview.catalog.agent import ask, describe
from eagleview.catalog.registry import approved
from eagleview.catalog.runner import Catalog, open_catalog
from eagleview.discovery.client import make_client, model_from
from eagleview.discovery.meter import Ledger, LedgerMeter
from eagleview.runner import environment
from eagleview.safety.redaction import Redactor


def reachable(url: str) -> bool:
    try:
        httpx2.get(url, timeout=3, follow_redirects=False)
    except httpx2.HTTPError:
        return False
    return True


async def main(question: str, tenant_slug: str = "lantern-bay") -> int:
    entries = approved()
    if not entries:
        print(
            "No approved capabilities yet: review one and run `eagleview approve`.", file=sys.stderr
        )
        return 2
    env = environment()
    tenant = load_tenant(tenant_path(tenant_slug))
    if not reachable(tenant.base_url):
        # Checked before any model call, so a missing bank never spends a run.
        print(
            f"The teller app at {tenant.base_url} isn't answering: start it with `make bank`.",
            file=sys.stderr,
        )
        return 2
    catalog = Catalog(
        entries,
        profile=load_app_profile(app_profile_path(tenant.app_profile)),
        tenant=tenant,
        policy=load_policy(Path("policies/default-teller.yaml")),
        overlays=tenant_overlays(tenant_slug),
        environ=env,
    )
    async with open_catalog(catalog):
        answer = await ask(
            question,
            client=make_client(env, Redactor()),
            model=model_from(env),
            meter=LedgerMeter(Ledger(Path("runs") / "discovery_ledger.jsonl")),
            catalog=catalog,
        )
    print(describe(answer))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1], *sys.argv[2:3])))
