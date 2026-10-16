"""Replaying against the in-process mock bank: each tenant's policy and tenant profile moved to
its ephemeral test port, and the run's evidence kept in a temporary directory."""

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.async_api import Browser

from eagleview.artifacts.loader import load_for_run
from eagleview.artifacts.yaml_io import dump_capability, load_app_profile, load_model, load_policy
from eagleview.replay.preflight import RunOptions
from eagleview.runner import run_replay
from eagleview.schema.capability import Approval, Capability, Provenance, Verification
from eagleview.schema.policy import Policy
from eagleview.schema.result import RunResult
from eagleview.schema.tenant import Credentials, TenantProfile
from tests.fixtures import APP_PROFILE, CAPABILITY, POLICY
from tests.support.bank import Bank


def policy_for(*banks: Bank, extra: dict[str, list[str]] | None = None) -> Policy:
    """The committed default policy, with each tenant's origin moved to its test port."""
    base = load_policy(POLICY)
    origins = {b.tenant.slug: [b.url] for b in banks} | (extra or {})
    return base.model_copy(update={"allowed_origins": origins})


def tenant_for(bank: Bank) -> TenantProfile:
    t = bank.tenant
    return TenantProfile(
        slug=t.slug,
        name=t.name,
        app_profile="ironbridge-teller",
        base_url=bank.url,
        credentials=Credentials(username=t.username, password=f"secret://{t.password_secret}"),
    )


@dataclass
class Replayed:
    result: RunResult
    run_dir: Path

    def events(self) -> list[dict[str, Any]]:
        lines = (self.run_dir / "run.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events() if e["kind"] == kind]

    def files_text(self) -> str:
        """Every text file the run wrote, concatenated."""
        parts = [
            p.read_text(encoding="utf-8")
            for p in sorted(self.run_dir.rglob("*"))
            if p.is_file() and p.suffix in (".jsonl", ".json", ".html")
        ]
        return "\n".join(parts)


Replay = Callable[..., Awaitable[Replayed]]


def make_replayer(browser: Browser, runs_root: Path) -> Replay:
    async def go(
        bank: Bank,
        inputs: dict[str, object],
        *,
        capability: Path = CAPABILITY,
        options: RunOptions | None = None,
        allow_modified: bool = False,
        environ: dict[str, str] | None = None,
    ) -> Replayed:
        loaded = load_for_run(capability, allow_modified=allow_modified)
        result = await run_replay(
            loaded.capability,
            inputs,
            profile=load_app_profile(APP_PROFILE),
            tenant=tenant_for(bank),
            policy=policy_for(bank),
            options=options,
            modified=loaded.modified,
            browser=browser,
            runs_root=runs_root,
            environ=environ if environ is not None else {bank.tenant.password_env: bank.password},
        )
        return Replayed(result, runs_root / result.run_id)

    return go


def share_rows(bank: Bank, member: str) -> int:
    """How many share accounts the bank shows for a member, read over HTTP like a teller would."""
    with bank.signed_on_client() as c:
        page = c.post("/inq", data={"txtA": member, "txtB": ""}).text
    section = page.split("Share Accounts", 1)[1].split("Loan Accounts", 1)[0]
    rows = re.findall(r"<tr>\s*<td><font face=\"Courier New\"", section)
    return len(rows)


def approved_copy(source: Path, tmp_path: Path) -> Path:
    """A copy of a fixture marked verified and approved, with a fresh hash. For tests only: real
    approvals come from `eagleview approve` after review (P7)."""
    cap = load_model(source.read_text(encoding="utf-8"), Capability)
    at = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    provenance = Provenance(
        verified_by=[Verification(run_id="r_test_verify", at=at)],
        approved_by=Approval(reviewer="test-reviewer", at=at),
    )
    approved = Capability.model_validate(
        cap.model_dump() | {"status": "approved", "provenance": provenance.model_dump()}
    )
    path = tmp_path / f"{cap.id}.approved.yaml"
    dump_capability(approved, path)
    return path
