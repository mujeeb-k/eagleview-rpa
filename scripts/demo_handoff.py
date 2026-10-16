"""`make demo-handoff`: the interactive takeover demo, with a person at the operator console.

The bank starts in-process, an unknown "Cash Drawer Alert" modal is armed on Member Detail, and
a replay of the member lookup runs with escalation on. When it stops, open the console URL
printed below, take control, tick "Supervisor notified", click Continue, and resume. Every wait
for you has a timeout (5 minutes) that ends the run cleanly with a message."""

import asyncio
import sys
from pathlib import Path

from eagleview.artifacts.loader import load_for_run
from eagleview.artifacts.yaml_io import load_app_profile, load_policy, load_tenant
from eagleview.control.interventions import InterventionDesk, InterventionStore
from eagleview.operator.console import create_console
from eagleview.operator.server import console_server
from eagleview.replay.preflight import RunOptions
from eagleview.runner import environment, run_replay
from eagleview.surfaces.web.launch import launched_browser
from mock_bank.harness import running_banks

REPO = Path(__file__).resolve().parents[1]
WAIT_S = 300


async def main() -> int:
    runs = REPO / "runs" / "demo-handoff"
    runs.mkdir(parents=True, exist_ok=True)
    with running_banks(REPO / ".env") as banks:
        bank = banks["lantern-bay"]
        bank.reset()
        bank.set_faults(unknown_modal_on="/member")
        tenant = load_tenant(REPO / "tenants" / "lantern-bay.yaml").model_copy(
            update={"base_url": bank.url}
        )
        policy = load_policy(REPO / "policies" / "training-teller.yaml").model_copy(
            update={"allowed_origins": {"lantern-bay": [bank.url]}}
        )
        store = InterventionStore(runs / "interventions.sqlite")
        desk = InterventionDesk(
            store,
            human_timeout_s=WAIT_S,
            notify=lambda line: print(line, file=sys.stderr, flush=True),
        )
        async with (
            console_server(create_console(desk, runs), port=8700) as url,
            launched_browser() as browser,
        ):
            desk.console_url = url
            print(f"Operator console: {url}  (waiting up to {WAIT_S // 60} minutes for you)")
            result = await run_replay(
                load_for_run(
                    REPO / "capabilities/drafts/member.lookup_share_balances.yaml"
                ).capability,
                {"member_id": "10482"},
                profile=load_app_profile(REPO / "app_profiles/ironbridge-teller.yaml"),
                tenant=tenant,
                policy=policy,
                options=RunOptions(escalate=True),
                browser=browser,
                runs_root=runs,
                environ={**environment(), bank.tenant.password_env: bank.password},
                desk=desk,
            )
        store.close()
    print(f"Run {result.run_id}: {result.status}")
    if result.status == "needs_human":
        print("Nobody handed the session back in time, so the run is parked as needs_human.")
    print(f"Evidence: {runs / result.run_id}")
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
