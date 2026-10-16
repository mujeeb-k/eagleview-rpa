"""Putting a replay together: policy, redactor, evidence writer, browser and engine. The CLI and
the tests come through here, so a run from either is wired the same way."""

import os
from collections.abc import Mapping
from contextlib import AsyncExitStack
from pathlib import Path

from dotenv import dotenv_values
from playwright.async_api import Browser

from eagleview.control.interventions import (
    DEFAULT_HUMAN_TIMEOUT_S,
    InterventionDesk,
    InterventionStore,
)
from eagleview.evidence.writer import EvidenceWriter
from eagleview.overlays.resolve import resolve
from eagleview.replay.engine import new_run_id, replay
from eagleview.replay.preflight import RunOptions
from eagleview.safety.policy_engine import PolicyEngine
from eagleview.safety.redaction import Redactor
from eagleview.schema.app_profile import AppProfile
from eagleview.schema.capability import Capability
from eagleview.schema.overlay import Overlay
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
    desk: InterventionDesk | None = None,
    console_port: int | None = None,
    human_timeout_s: float = DEFAULT_HUMAN_TIMEOUT_S,
    overlays: list[Overlay] | None = None,
    record_video_dir: Path | None = None,
) -> RunResult:
    """Replay once. Opens its own browser unless one is passed in.

    `overlays` are applied only when given (P8): the caller decides which tenant overlays apply.

    With `console_port`, a human can be brought in: an intervention desk (SQLite under
    `runs_root`) and the operator console run in this process for the length of the run."""
    writer = EvidenceWriter(runs_root, run_id or new_run_id(), Redactor.from_policy(policy))
    async with AsyncExitStack() as stack:
        if console_port is not None:
            from eagleview.operator.console import create_console
            from eagleview.operator.server import console_server

            if desk is None:
                store = InterventionStore(runs_root / "interventions.sqlite")
                stack.callback(store.close)
                desk = InterventionDesk(store, human_timeout_s=human_timeout_s)
            url = await stack.enter_async_context(
                console_server(create_console(desk, runs_root), port=console_port)
            )
            desk.console_url = url
        if browser is None:
            browser = await stack.enter_async_context(launched_browser(headed=headed))
        effective = resolve(capability, profile, tenant, overlays or [])
        opener = surface_opener(
            browser,
            PolicyEngine(policy),
            sink=writer,
            label_aliases=effective.label_aliases,
            record_video_dir=record_video_dir,  # the demo video only; never set by default
        )
        return await replay(
            effective.capability,
            inputs,
            profile=effective.profile,
            tenant=tenant,
            open_surface=opener,
            writer=writer,
            environ=environ if environ is not None else environment(),
            options=options,
            modified=modified,
            desk=desk,
            label_aliases=effective.label_aliases,
            effective_hash=effective.effective_hash,
        )
    raise AssertionError("unreachable")  # pragma: no cover
