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
