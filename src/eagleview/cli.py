"""Eagleview command-line interface."""

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from eagleview import __version__
from eagleview.replay.preflight import DEFAULT_DEADLINE_S
from eagleview.schema.result import RunResult

# Exit codes for `eagleview replay`. 2 is left to usage errors, as Typer reports them.
EXIT_CODES = {"success": 0, "outcome": 3, "rejected": 4, "failed": 5, "needs_human": 6}

app = typer.Typer(no_args_is_help=True, help="Eagleview: discover once, replay deterministically.")
schema_app = typer.Typer(no_args_is_help=True, help="Artifact schemas.")
app.add_typer(schema_app, name="schema")


@app.callback()
def main() -> None:
    """Eagleview command-line interface."""


@app.command()
def version() -> None:
    """Print the installed Eagleview version."""
    typer.echo(__version__)


@schema_app.command("export")
def schema_export(
    out: Annotated[Path, typer.Option(help="Directory for the *.schema.json files.")] = Path(
        "schemas"
    ),
) -> None:
    """Write JSON Schema for every artifact type, generated from the Pydantic models."""
    from eagleview.artifacts.json_schema import export_schemas

    for path in export_schemas(out):
        typer.echo(path)


REPLAY_HELP = (
    "Replay a capability deterministically, with no LLM.\n\n"
    "CAPABILITY is a YAML path, or an id looked up in capabilities/. Give inputs as "
    "--input name=value, once per input.\n\n"
    "Input normalization: string inputs are trimmed of surrounding whitespace before strict "
    "validation, and nothing else. There is no case folding, digit extraction or zero padding, "
    "so ' 10482 ' runs as 10482 while '10 482' is rejected as INPUT_INVALID.\n\n"
    "The result JSON goes to stdout and a short summary to stderr. Evidence goes to "
    "runs/<run_id>/, redacted. Exit codes: 0 success, 3 business outcome, 4 rejected, "
    f"5 failed, 6 needs a human. The run deadline defaults to {DEFAULT_DEADLINE_S:g} seconds."
)


@app.command(help=REPLAY_HELP)
def replay(
    capability: Annotated[str, typer.Argument(help="A capability YAML path or id.")],
    inputs: Annotated[
        list[str] | None,
        typer.Option("--input", "-i", help="name=value; repeat for each input."),
    ] = None,
    tenant: Annotated[str, typer.Option(help="Tenant slug, from tenants/.")] = "lantern-bay",
    escalate: Annotated[
        bool, typer.Option(help="Park a hard failure for a human (needs_human).")
    ] = False,
    approve_gates: Annotated[
        bool,
        typer.Option(help="Approve this run's irreversible steps. Never with --unattended."),
    ] = False,
    unattended: Annotated[
        bool,
        typer.Option(help="No human at hand: needs an approved capability, never self-approves."),
    ] = False,
    allow_modified: Annotated[
        bool,
        typer.Option(help="Run an edited verified/approved artifact as a draft (attended only)."),
    ] = False,
    headed: Annotated[
        bool, typer.Option(help="Show the browser, slowed down to watch. Same path as headless.")
    ] = False,
    deadline: Annotated[
        float, typer.Option(help="Seconds the whole run may take.")
    ] = DEFAULT_DEADLINE_S,
    policy: Annotated[Path, typer.Option(help="Safety policy YAML.")] = Path(
        "policies/default-teller.yaml"
    ),
    tenant_file: Annotated[
        Path | None, typer.Option(help="A tenant YAML to use instead of tenants/<slug>.yaml.")
    ] = None,
    runs_dir: Annotated[Path, typer.Option(help="Where run evidence is written.")] = Path("runs"),
    console_port: Annotated[
        int, typer.Option(help="Operator console port, started when a human may be needed.")
    ] = 8700,
    human_timeout: Annotated[
        float, typer.Option(help="Seconds to wait for a human before parking the run.")
    ] = 900.0,
) -> None:
    from eagleview.artifacts.loader import (
        app_profile_path,
        capability_path,
        load_for_run,
        tenant_path,
    )
    from eagleview.artifacts.yaml_io import (
        ArtifactHashMismatch,
        load_app_profile,
        load_policy,
        load_tenant,
    )
    from eagleview.replay.preflight import RunOptions
    from eagleview.runner import run_replay

    if unattended and approve_gates:
        raise typer.BadParameter(
            "an unattended run never approves its own irreversible steps",
            param_hint="--approve-gates with --unattended",
        )
    if unattended and allow_modified:
        raise typer.BadParameter(
            "an edited artifact never runs unattended",
            param_hint="--allow-modified with --unattended",
        )
    raw: dict[str, object] = {}
    for item in inputs or []:
        name, sep, value = item.partition("=")
        if not sep or not name:
            raise typer.BadParameter(f"expected name=value, got {item!r}", param_hint="--input")
        raw[name] = value
    try:
        loaded = load_for_run(capability_path(capability), allow_modified=allow_modified)
    except (FileNotFoundError, ArtifactHashMismatch) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None
    cap = loaded.capability
    profile = load_app_profile(app_profile_path(cap.app.profile))
    tenant_profile = load_tenant(tenant_file or tenant_path(tenant))
    options = RunOptions(
        unattended=unattended, approve_gates=approve_gates, escalate=escalate, deadline_s=deadline
    )
    # A human may be needed: start the console. Gated steps ask it unless --approve-gates.
    gated = any(step.requires_approval for step in cap.steps)
    wants_console = escalate or (gated and not approve_gates)
    from eagleview.operator.server import ConsolePortInUse

    try:
        result = asyncio.run(
            run_replay(
                cap,
                raw,
                profile=profile,
                tenant=tenant_profile,
                policy=load_policy(policy),
                options=options,
                modified=loaded.modified,
                headed=headed,
                runs_root=runs_dir,
                console_port=console_port if wants_console else None,
                human_timeout_s=human_timeout,
            )
        )
    except ConsolePortInUse as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None
    typer.echo(result.model_dump_json(indent=2, exclude_none=True))
    typer.echo(_summary(result, runs_dir), err=True)
    raise typer.Exit(EXIT_CODES[result.status])


def _summary(result: RunResult, runs_dir: Path) -> str:
    seconds = result.timing_ms.get("total", 0) / 1000
    lines = [f"{result.capability} on {result.tenant}: {result.status} in {seconds:.1f}s"]
    if result.outcome is not None:
        at = f" at {result.outcome.at_step}" if result.outcome.at_step else ""
        lines.append(f"  outcome {result.outcome.code}{at}: {result.outcome.message}")
    if result.rejection is not None:
        lines.append(f"  rejected {result.rejection.code}")
        if result.rejection.message:
            lines.append(f"    {result.rejection.message}")
        lines += [f"    {e.field}: {e.code} ({e.message})" for e in result.rejection.errors]
    if result.failure is not None:
        f = result.failure
        at = f" at {f.step_id}" if f.step_id else ""
        retry = "retryable" if f.retryable else "not retryable"
        lines.append(f"  {f.category}{at} ({retry}): {f.message}")
    for r in result.recoveries:
        lines.append(f"  recovered: {r.handler} at {r.at_step}")
    for i in result.interventions:
        lines.append(f"  {i.kind}: {i.resolution}")
    if result.modified:
        lines.append("  modified: an edited artifact, run as a draft")
    lines.append(f"  evidence: {runs_dir / result.run_id}")
    return "\n".join(lines)


@app.command(
    help=(
        "Discover a task with Claude, then compile, verify and probe it: Claude drives the app "
        "once through the screen, every action is grounded and recorded, and the trace becomes a "
        "capability under capabilities/drafts/ (never overwriting a file). Needs "
        "ANTHROPIC_API_KEY. Spend is ledgered in runs/discovery_ledger.jsonl and capped at $25 "
        "and 30 runs. The operator console runs while it works, for hand-overs and approvals."
    )
)
def discover(
    goal: Annotated[str, typer.Option(help="What a teller wants done, in plain words.")],
    tenant: Annotated[str, typer.Option(help="Tenant slug, from tenants/.")] = "lantern-bay",
    model: Annotated[
        str | None, typer.Option(help="Model id; default EAGLEVIEW_MODEL, else claude-sonnet-5-5.")
    ] = None,
    effort: Annotated[str, typer.Option(help="Effort level for the loop.")] = "medium",
    policy: Annotated[Path, typer.Option(help="Safety policy YAML.")] = Path(
        "policies/default-teller.yaml"
    ),
    tenant_file: Annotated[
        Path | None, typer.Option(help="A tenant YAML to use instead of tenants/<slug>.yaml.")
    ] = None,
    runs_dir: Annotated[Path, typer.Option(help="Where run evidence is written.")] = Path("runs"),
    drafts_dir: Annotated[Path, typer.Option(help="Where compiled drafts go.")] = Path(
        "capabilities/drafts"
    ),
    console_port: Annotated[int, typer.Option(help="Operator console port.")] = 8700,
    human_timeout: Annotated[
        float, typer.Option(help="Seconds to wait for a person at the console.")
    ] = 900.0,
    headed: Annotated[bool, typer.Option(help="Show the browser.")] = False,
) -> None:
    from eagleview.artifacts.loader import app_profile_path, tenant_path
    from eagleview.artifacts.yaml_io import load_app_profile, load_policy, load_tenant
    from eagleview.compiler.pipeline import PipelineResult, discover_and_compile
    from eagleview.control.interventions import InterventionDesk, InterventionStore
    from eagleview.discovery.agent_loop import DiscoveryConfig
    from eagleview.discovery.client import model_from
    from eagleview.discovery.meter import Ledger, LedgerMeter
    from eagleview.operator.console import create_console
    from eagleview.operator.server import ConsolePortInUse, console_server
    from eagleview.runner import environment
    from eagleview.surfaces.web.launch import launched_browser

    env = environment()
    tenant_profile = load_tenant(tenant_file or tenant_path(tenant))
    profile = load_app_profile(app_profile_path(tenant_profile.app_profile))
    config = DiscoveryConfig(goal=goal, model=model_from(env, model), effort=effort)
    meter = LedgerMeter(Ledger(runs_dir / "discovery_ledger.jsonl"))
    store = InterventionStore(runs_dir / "interventions.sqlite")
    desk = InterventionDesk(store, human_timeout_s=human_timeout)

    async def go() -> PipelineResult:
        async with (
            console_server(create_console(desk, runs_dir), port=console_port) as url,
            launched_browser(headed=headed) as browser,
        ):
            desk.console_url = url
            return await discover_and_compile(
                config,
                profile=profile,
                tenant=tenant_profile,
                policy=load_policy(policy),
                browser=browser,
                meter=meter,
                environ=env,
                runs_root=runs_dir,
                drafts_root=drafts_dir,
                desk=desk,
            )

    try:
        result = asyncio.run(go())
    except ConsolePortInUse as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None
    finally:
        store.close()
    typer.echo(result.discovery.model_dump_json(indent=2))
    spend = meter.ledger.spend()
    typer.echo(result.review(), err=True)
    typer.echo(f"all discovery so far: ${spend.usd:.2f} over {spend.runs} runs", err=True)
    raise typer.Exit(0 if result.status == "verified" else 5)


@app.command(
    help=(
        "Approve a verified capability after reviewing it: sets status approved, records the "
        "reviewer in provenance, and promotes it from capabilities/drafts/ into capabilities/. "
        "A person's step: unattended runs and the agent catalog only use approved capabilities."
    )
)
def approve(
    capability: Annotated[Path, typer.Argument(help="The verified draft YAML.")],
    reviewer: Annotated[str, typer.Option(help="Who reviewed it.")],
    into: Annotated[Path, typer.Option(help="Where approved capabilities live.")] = Path(
        "capabilities"
    ),
) -> None:
    from datetime import UTC, datetime

    from eagleview.artifacts.yaml_io import ArtifactHashMismatch, dump_capability, load_capability
    from eagleview.schema.capability import Approval, Capability

    try:
        cap = load_capability(capability)
    except ArtifactHashMismatch as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None
    if cap.status != "verified":
        typer.echo(f"error: only a verified capability can be approved ({cap.status})", err=True)
        raise typer.Exit(2)
    target = into / f"{cap.id}.yaml"
    if target.exists():
        typer.echo(
            f"error: {target} exists; review the difference and move it aside first", err=True
        )
        raise typer.Exit(2)
    provenance = cap.provenance.model_copy(
        update={"approved_by": Approval(reviewer=reviewer, at=datetime.now(UTC))}
    )
    approved = Capability.model_validate(
        cap.model_copy(update={"provenance": provenance}).model_dump() | {"status": "approved"}
    )
    written = dump_capability(approved, target)
    typer.echo(f"approved {written.ref} by {reviewer}: {target}")
