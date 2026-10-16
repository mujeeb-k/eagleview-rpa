"""discover -> compile -> verify -> probe, in one call. What `eagleview discover` runs.

The result is a `verified` capability under `capabilities/drafts/`, never overwriting a file,
or a draft with notes saying what is missing. Nothing here approves: approval is a person's
step (`eagleview approve`)."""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import httpx2
from playwright.async_api import Browser

from eagleview.artifacts.loader import DRAFTS, write_draft
from eagleview.compiler.assemble import assemble
from eagleview.compiler.naming import apply_names, propose_names
from eagleview.compiler.probe import classify, propose_probes, read_probe
from eagleview.compiler.prune import prune
from eagleview.compiler.segment import CompileError, segment
from eagleview.control.interventions import InterventionDesk
from eagleview.discovery.agent_loop import DiscoveryConfig, DiscoveryResult, discover
from eagleview.discovery.client import StructuredRefused, make_client
from eagleview.discovery.meter import BudgetExceeded, Meter
from eagleview.replay.preflight import RunOptions
from eagleview.runner import run_replay
from eagleview.safety.redaction import Redactor
from eagleview.schema.app_profile import AppProfile
from eagleview.schema.capability import Capability, Outcome, Verification
from eagleview.schema.common import Source
from eagleview.schema.policy import Policy
from eagleview.schema.result import RunResult
from eagleview.schema.tenant import TenantProfile

Status = Literal["verified", "draft", "failed"]


@dataclass
class PipelineResult:
    discovery: DiscoveryResult
    status: Status
    capability: Capability | None = None
    path: Path | None = None
    notes: list[str] = field(default_factory=list[str])
    replays: dict[str, str] = field(default_factory=dict[str, str])  # purpose -> run id
    usd: Decimal = Decimal(0)

    def review(self) -> str:
        """A short summary for a person to review before approving."""
        lines = [f"discovery {self.discovery.status} ({self.discovery.stop_reason})"]
        cap = self.capability
        if cap is not None:
            lines.append(f"{cap.ref}: {self.status}, risk {cap.risk}")
            for step in cap.steps:
                fragile = " (fragile target)" if step.target and step.target.fragile else ""
                lines.append(f"  {step.id}: {step.action.type}{fragile}")
            lines.append(f"  outputs: {', '.join(cap.outputs) or '(none)'}")
            outcomes = [f"{o.code} [{o.source}]" for o in cap.outcomes]
            lines.append(f"  outcomes: {', '.join(outcomes) or '(none)'}")
        if self.path is not None:
            lines.append(f"  written to {self.path}")
        lines += [f"  note: {n}" for n in self.notes]
        lines.append(f"  model spend this pipeline: ${self.usd}")
        return "\n".join(lines)


def _records(runs_root: Path, run_id: str) -> list[dict[str, Any]]:
    path = runs_root / run_id / "trace.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


async def discover_and_compile(
    config: DiscoveryConfig,
    *,
    profile: AppProfile,
    tenant: TenantProfile,
    policy: Policy,
    browser: Browser,
    meter: Meter,
    environ: dict[str, str],
    runs_root: Path,
    drafts_root: Path = DRAFTS,
    desk: InterventionDesk | None = None,
    http_client: httpx2.AsyncClient | None = None,
    record_video_dir: Path | None = None,
) -> PipelineResult:
    found = await discover(
        config,
        profile=profile,
        tenant=tenant,
        policy=policy,
        browser=browser,
        meter=meter,
        environ=environ,
        runs_root=runs_root,
        desk=desk,
        http_client=http_client,
        record_video_dir=record_video_dir,  # the demo video only; never set by default
    )
    result = PipelineResult(found, "failed", usd=found.usd)
    if found.status != "success":
        result.notes.append("discovery did not succeed, so there is nothing to compile")
        return _done(result, meter)
    client = make_client(environ, Redactor(), http_client=http_client)
    rid = found.run_id
    values = found.values
    try:
        seg = segment(_records(runs_root, rid), values)
        cap = assemble(
            seg,
            prune(seg.steps),
            goal=config.goal,
            inputs=found.inputs,
            profile=profile,
            run_id=rid,
            model=config.model,
        )
        try:
            names = await propose_names(client, config.model, meter, rid, cap, config.goal, values)
        except StructuredRefused as exc:
            names = {}
            result.notes.append(f"naming pass returned nothing ({exc}); mechanical names kept")
        cap, rejected = apply_names(cap, names, values)
        result.notes += [f"naming proposal rejected: {r}" for r in rejected]
    except CompileError as exc:
        result.notes.append(f"compile failed: {exc}")
        return _done(result, meter)
    except BudgetExceeded as exc:
        result.notes.append(f"stopped by the spend limit: {exc}")
        return _done(result, meter)
    result.capability, result.status = cap, "draft"

    async def replay(c: Capability, inputs: dict[str, str], options: RunOptions) -> RunResult:
        return await run_replay(
            c,
            inputs,
            profile=profile,
            tenant=tenant,
            policy=policy,
            options=options,
            browser=browser,
            runs_root=runs_root,
            environ=environ,
            desk=None if options.probe else desk,
        )

    needs_person = cap.risk == "irreversible"
    if needs_person and desk is None:
        result.notes.append("irreversible: verifying needs a person to approve in the console")
        result.path = write_draft(cap, drafts_root)
        return _done(result, meter)
    first = await replay(cap, values, RunOptions())
    result.replays["verify"] = first.run_id
    if first.status != "success":
        why = first.failure.message if first.failure else first.status
        result.notes.append(f"verify replay did not succeed ({first.status}): {why}")
        result.path = write_draft(cap, drafts_root)
        return _done(result, meter)

    outcomes: list[Outcome] = []
    if cap.inputs and not needs_person:
        try:
            outcomes = await _probe(
                cap, values, client, config.model, meter, rid, replay, result, runs_root
            )
        except BudgetExceeded as exc:
            result.notes.append(f"probing stopped by the spend limit: {exc}")
    elif needs_person:
        result.notes.append("probing skipped: an irreversible capability is never probed")
    cap = cap.model_copy(update={"outcomes": outcomes})
    verified_runs = [first.run_id]
    if outcomes:
        # Outcomes were added: prove the success path still succeeds with them in place. Without
        # new outcomes the first verify stands, so an irreversible flow is not posted again.
        final = await replay(cap, values, RunOptions())
        result.replays["final_verify"] = final.run_id
        if final.status != "success":
            result.notes.append(f"the final verify failed with outcomes in place ({final.status})")
            result.capability, result.path = cap, write_draft(cap, drafts_root)
            return _done(result, meter)
        verified_runs.append(final.run_id)
    at = datetime.now(UTC)
    provenance = cap.provenance.model_copy(
        update={"verified_by": [Verification(run_id=r, at=at) for r in verified_runs]}
    )
    verified = Capability.model_validate(
        cap.model_copy(update={"provenance": provenance}).model_dump() | {"status": "verified"}
    )
    result.capability, result.status = verified, "verified"
    result.path = write_draft(verified, drafts_root)
    return _done(result, meter)


def _done(result: PipelineResult, meter: Meter) -> PipelineResult:
    """Every model call of the pipeline, discovery's included, is ledgered under its run id."""
    ledger = getattr(meter, "ledger", None)
    if ledger is not None:
        result.usd = ledger.run_spend(result.discovery.run_id)
    else:
        result.usd = sum((u for _, u in getattr(meter, "entries", [])), Decimal(0))
    return result


async def _probe(
    cap: Capability,
    values: dict[str, str],
    client: Any,
    model: str,
    meter: Meter,
    rid: str,
    replay: Any,
    result: PipelineResult,
    runs_root: Path,
) -> list[Outcome]:
    probes = await propose_probes(client, model, meter, rid, cap, values)
    if not probes:
        result.notes.append("probing: no valid probe inputs were proposed")
    kept: dict[str, Outcome] = {}
    seen: set[str] = set()
    for n, probe in enumerate(probes, 1):
        run = await replay(cap, probe, RunOptions(probe=True))
        result.replays[f"probe_{n}"] = run.run_id
        if run.status == "success":
            result.notes.append(f"probe {n} found a real record, so it shows no outcome")
            continue
        state = read_probe(runs_root / run.run_id)
        if state is None:
            result.notes.append(f"probe {n} ended {run.status} without a capturable screen")
            continue
        key = json.dumps(sorted(t["text"] for t in state.get("texts", [])))
        if key in seen:
            continue
        seen.add(key)
        outcome, why = await classify(client, model, meter, rid, state, probe)
        if outcome is None or outcome.code in kept:
            result.notes.append(f"probe {n}: {why}")
            continue
        trial = cap.model_copy(update={"outcomes": [*kept.values(), outcome]})
        confirm = await replay(trial, probe, RunOptions(probe=True))
        result.replays[f"confirm_{outcome.code.lower()}"] = confirm.run_id
        if confirm.status == "outcome" and confirm.outcome and confirm.outcome.code == outcome.code:
            kept[outcome.code] = outcome.model_copy(update={"source": Source.probed_verified})
        else:
            result.notes.append(f"{outcome.code} dropped: the confirm run gave {confirm.status}")
    return list(kept.values())
