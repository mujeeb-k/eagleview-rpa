"""Putting a replay together: policy, redactor, evidence writer, browser and engine. The CLI and
the tests come through here, so a run from either is wired the same way."""

import os
from collections.abc import Mapping
from contextlib import AsyncExitStack
from pathlib import Path

from dotenv import dotenv_values
from playwright.async_api import Browser

from eagleview.evidence.writer import EvidenceWriter
from eagleview.replay.engine import new_run_id, replay
from eagleview.replay.preflight import RunOptions
from eagleview.safety.policy_engine import PolicyEngine
from eagleview.safety.redaction import Redactor
from eagleview.schema.app_profile import AppProfile
from eagleview.schema.capability import Capability
from eagleview.schema.policy import Policy
from eagleview.schema.result import RunResult
from eagleview.schema.tenant import TenantProfile
from eagleview.surfaces.web.launch import launched_browser, surface_opener

RUNS = Path("runs")


def environment(env_file: Path | None = Path(".env")) -> dict[str, str]:
    """The process environment over the values in `.env`, where `make bank` keeps the training
    passwords. A variable set in the shell wins."""
    values: dict[str, str] = {}
    if env_file is not None and env_file.is_file():
        values = {k: v for k, v in dotenv_values(env_file).items() if v is not None}
    return values | dict(os.environ)


async def run_replay(
    capability: Capability,
    inputs: Mapping[str, object],
    *,
    profile: AppProfile,
    tenant: TenantProfile,
    policy: Policy,
    options: RunOptions | None = None,
    modified: bool = False,
    browser: Browser | None = None,
    headed: bool = False,
    runs_root: Path = RUNS,
    environ: Mapping[str, str] | None = None,
    run_id: str | None = None,
) -> RunResult:
    """Replay once. Opens its own browser unless one is passed in."""
    writer = EvidenceWriter(runs_root, run_id or new_run_id(), Redactor.from_policy(policy))
    async with AsyncExitStack() as stack:
        if browser is None:
            browser = await stack.enter_async_context(launched_browser(headed=headed))
        opener = surface_opener(browser, PolicyEngine(policy), sink=writer)
        return await replay(
            capability,
            inputs,
            profile=profile,
            tenant=tenant,
            open_surface=opener,
            writer=writer,
            environ=environ if environ is not None else environment(),
            options=options,
            modified=modified,
        )
    raise AssertionError("unreachable")  # pragma: no cover
