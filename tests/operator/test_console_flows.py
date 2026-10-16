"""The human path, end to end: a replay against the real mock bank, the operator console serving
in the same event loop, and a SCRIPTED operator driving it over the console's HTTP API only."""

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import websockets

from eagleview.artifacts.yaml_io import load_overlay
from eagleview.control.interventions import InterventionDesk, InterventionStore
from eagleview.operator.console import create_console
from eagleview.operator.server import ConsolePortInUse, console_server
from eagleview.replay.preflight import RunOptions
from eagleview.schema.app_profile import ClickSequence
from tests.fixtures import OPEN_SHARE
from tests.support.bank import Bank
from tests.support.operator import ScriptedOperator
from tests.support.replay import Replay, Replayed, approved_copy, share_rows

DEMO = "10482"
SHARE = {"member_id": DEMO, "share_type": "Money Market"}
ESCALATE = RunOptions(escalate=True)


class Console:
    def __init__(self, desk: InterventionDesk, url: str, operator: ScriptedOperator) -> None:
        self.desk, self.url, self.op = desk, url, operator


@pytest.fixture
async def console(tmp_path: Path) -> AsyncIterator[Console]:
    store = InterventionStore(tmp_path / "interventions.sqlite")
    desk = InterventionDesk(store, human_timeout_s=30, notify=lambda _line: None)
    async with console_server(create_console(desk, tmp_path / "runs"), port=0) as url:
        desk.console_url = url
        operator = ScriptedOperator(url)
        yield Console(desk, url, operator)
        await operator.close()
    store.close()


def start(replay: Replay, *args: object, **kwargs: object) -> asyncio.Task[Replayed]:
    return asyncio.create_task(replay(*args, **kwargs))  # type: ignore[arg-type]


# --- takeover -------------------------------------------------------------------------------------


async def test_operator_clears_an_unknown_modal_and_the_run_completes(
    replay: Replay, lantern: Bank, console: Console
) -> None:
    lantern.set_faults(unknown_modal_on="/member")
    task = start(replay, lantern, {"member_id": DEMO}, options=ESCALATE, desk=console.desk)
    op = console.op
    iv = await op.wait_for_intervention("takeover")
    run_id = iv["run_id"]
    assert (iv["reason"], iv["step_id"]) == ("UNKNOWN_STATE", "submit_search")
    assert (
        iv["screenshot"]
        and (await op.http.get(f"/evidence/{run_id}/{iv['screenshot']}")).is_success
    )
    assert (await op.state(run_id))["state"] == "AWAITING_HUMAN"

    taken = await op.take(run_id)
    assert (taken["state"], taken["holder"]) == ("HUMAN", "operator:scripted")
    # The live view streams frames while the human works. They go to this socket, never to disk.
    ws_url = console.url.replace("http://", "ws://") + f"/sessions/{run_id}/screencast"
    async with websockets.connect(ws_url) as ws:
        await op.click_text(run_id, "Supervisor notified", dx=-9)  # the checkbox left of its label
        frame = await asyncio.wait_for(ws.recv(), 5)
        assert base64.b64decode(__import__("json").loads(frame)["data"])[:2] == b"\xff\xd8"
    await op.click_text(run_id, "Continue")
    await op.resume(run_id, note="Supervisor notified; cleared the drawer alert.")

    run = await task
    r = run.result
    assert r.status == "success", r.failure
    assert r.outputs is not None and r.outputs["share_accounts"]
    [record] = [i for i in r.interventions if i.kind == "takeover"]
    assert record.id == iv["id"]
    assert record.resolution == "resumed by operator:scripted"
    assert record.human_actions == 2
    stored = console.desk.store.get(iv["id"])
    assert stored is not None and stored.resolution == "resumed"
    assert stored.note == "Supervisor notified; cleared the drawer alert."
    human = [e for e in run.of("surface.act") if e["actor"] == "operator:scripted"]
    assert [e["action"] for e in human] == ["click", "click"]

    # What the human did comes back as a draft overlay for review, never applied automatically.
    overlay = load_overlay(run.run_dir / "proposed_overlay.yaml")
    assert overlay.source == "human_demonstrated" and overlay.tenant == "lantern-bay"
    [state] = overlay.global_states_add
    assert state.source == "human_demonstrated"
    assert state.detect.model_dump() == {"text_present": {"frame": [], "text": "Cash Drawer Alert"}}
    assert isinstance(state.recover, ClickSequence) and len(state.recover.clicks) == 2

    # The request itself is kept in the run folder, written through the redacting writer.
    kept = json.loads((run.run_dir / f"intervention_{iv['id'].lower()}.json").read_text())
    assert kept["capability"] == r.capability
    assert (kept["kind"], kept["step_id"], kept["reason"]) == (
        "takeover",
        "submit_search",
        "UNKNOWN_STATE",
    )
    assert kept["state_summary"] and (run.run_dir / kept["screenshot"]).is_file()
    assert (kept["resolution"], kept["resolved_by"]) == ("resumed", "operator:scripted")


async def test_abort_ends_the_run_aborted_by_operator(
    replay: Replay, lantern: Bank, console: Console
) -> None:
    lantern.set_faults(unknown_modal_on="/member")
    task = start(replay, lantern, {"member_id": DEMO}, options=ESCALATE, desk=console.desk)
    iv = await console.op.wait_for_intervention("takeover")
    await console.op.abort(iv["run_id"], note="Wrong member; stopping.")
    r = (await task).result
    assert r.status == "failed"
    assert r.failure is not None and r.failure.category == "ABORTED_BY_OPERATOR"
    stored = console.desk.store.get(iv["id"])
    assert stored is not None and stored.resolution == "aborted"


async def test_resuming_into_an_unrecognized_state_is_a_handoff_mismatch(
    replay: Replay, lantern: Bank, console: Console
) -> None:
    lantern.set_faults(unknown_modal_on="/member")
    task = start(replay, lantern, {"member_id": DEMO}, options=ESCALATE, desk=console.desk)
    op = console.op
    iv = await op.wait_for_intervention("takeover")
    run_id = iv["run_id"]
    await op.take(run_id)
    await op.click_text(run_id, "Teller Totals")  # wander off to another screen
    await op.resume(run_id)
    r = (await task).result
    assert r.status == "failed"
    assert r.failure is not None
    assert (r.failure.category, r.failure.step_id) == ("HANDOFF_STATE_MISMATCH", "submit_search")


async def test_force_take_preempts_automation_at_its_next_action(
    replay: Replay, lantern: Bank, console: Console
) -> None:
    lantern.set_faults(slow_ms="/inq:2500")
    task = start(replay, lantern, {"member_id": DEMO}, desk=console.desk)
    op = console.op
    run_id = await op.wait_for_session()
    await op.wait_for_step(run_id, "open_member_inquiry")
    taken = await op.take(run_id, force=True)
    assert taken["holder"] == "operator:scripted"
    iv = await op.wait_for_intervention("takeover")
    # The lease moved at once: whichever step automation acts in next is refused, and that is
    # where the takeover is recorded.
    assert iv["reason"] == "OPERATOR_TAKEOVER"
    assert iv["step_id"] in ("open_member_inquiry", "enter_member_id")
    await op.resume(run_id)
    run = await task
    assert run.result.status == "success", run.result.failure
    refused = [e for e in run.of("surface.act") if e["status"] == "not_holder"]
    assert [(e["actor"], e["step_id"]) for e in refused] == [("automation", iv["step_id"])]


async def test_pause_yields_at_the_next_step_boundary(
    replay: Replay, lantern: Bank, console: Console
) -> None:
    lantern.set_faults(slow_ms="/inq:2000")
    task = start(replay, lantern, {"member_id": DEMO}, desk=console.desk)
    op = console.op
    run_id = await op.wait_for_session()
    await op.wait_for_step(run_id, "open_member_inquiry")
    await op.pause(run_id)
    iv = await op.wait_for_intervention("takeover")
    assert (iv["reason"], iv["step_id"]) == ("OPERATOR_PAUSE", "enter_member_id")
    await op.take(run_id)
    await op.resume(run_id)
    run = await task
    assert run.result.status == "success", run.result.failure
    assert not [e for e in run.of("surface.act") if e["status"] == "not_holder"]


# --- approvals ------------------------------------------------------------------------------------


async def test_operator_approves_the_post_and_one_share_opens(
    replay: Replay, lantern: Bank, console: Console
) -> None:
    before = share_rows(lantern, DEMO)
    task = start(replay, lantern, SHARE, capability=OPEN_SHARE, desk=console.desk)
    iv = await console.op.wait_for_intervention("approval")
    assert (iv["step_id"], iv["reason"]) == ("post_share", "APPROVAL_REQUIRED")
    await console.op.approve(iv["id"], note="Member asked for a money market share.")
    r = (await task).result
    assert r.status == "success", r.failure
    assert [(i.kind, i.resolution) for i in r.interventions] == [
        ("approval", "approved by operator:scripted")
    ]
    assert share_rows(lantern, DEMO) == before + 1


async def test_operator_denies_the_post_and_nothing_opens(
    replay: Replay, lantern: Bank, console: Console
) -> None:
    before = share_rows(lantern, DEMO)
    task = start(replay, lantern, SHARE, capability=OPEN_SHARE, desk=console.desk)
    iv = await console.op.wait_for_intervention("approval")
    await console.op.deny(iv["id"], note="Not requested by the member.")
    r = (await task).result
    assert r.status == "failed"
    assert r.failure is not None and r.failure.category == "POLICY_BLOCKED"
    assert "denied by operator:scripted" in (r.failure.message or "")
    assert share_rows(lantern, DEMO) == before


async def test_unanswered_approval_parks_the_run_without_posting(
    replay: Replay, lantern: Bank, console: Console
) -> None:
    console.desk.human_timeout_s = 1.5
    before = share_rows(lantern, DEMO)
    run = await replay(lantern, SHARE, capability=OPEN_SHARE, desk=console.desk)
    r = run.result
    assert r.status == "needs_human"
    assert r.failure is not None and r.failure.step_id == "post_share"
    assert (r.interventions[0].resolution or "").startswith("timed_out")
    assert share_rows(lantern, DEMO) == before


async def test_an_unattended_run_asks_a_human_and_never_approves_itself(
    replay: Replay, lantern: Bank, console: Console, tmp_path: Path
) -> None:
    approved = approved_copy(OPEN_SHARE, tmp_path)
    before = share_rows(lantern, DEMO)
    task = start(
        replay,
        lantern,
        SHARE,
        capability=approved,
        options=RunOptions(unattended=True),
        desk=console.desk,
    )
    iv = await console.op.wait_for_intervention("approval")
    assert share_rows(lantern, DEMO) == before  # blocked until a human answers
    await console.op.approve(iv["id"])
    assert (await task).result.status == "success"
    assert share_rows(lantern, DEMO) == before + 1


async def test_a_takeover_after_post_never_posts_again(
    replay: Replay, lantern: Bank, console: Console
) -> None:
    before = share_rows(lantern, DEMO)
    lantern.set_faults(error500_on="/share/posted")
    task = start(
        replay,
        lantern,
        SHARE,
        capability=OPEN_SHARE,
        options=RunOptions(escalate=True, approve_gates=True),
        desk=console.desk,
    )
    iv = await console.op.wait_for_intervention("takeover")
    assert (iv["reason"], iv["step_id"]) == ("OUTCOME_UNCERTAIN", "post_share")
    await console.op.take(iv["run_id"])
    await console.op.resume(iv["run_id"], note="Looked; leaving it for reconciliation.")
    run = await task
    r = run.result
    assert r.status == "failed"
    assert r.failure is not None and r.failure.category == "OUTCOME_UNCERTAIN"
    assert "HANDOFF_STATE_MISMATCH" in (r.failure.message or "")
    posts = [e for e in run.of("surface.act") if e.get("step_id") == "post_share"]
    assert len(posts) == 1
    assert share_rows(lantern, DEMO) == before + 1


# --- the console itself ---------------------------------------------------------------------------


async def test_console_refuses_bad_operator_names_and_illegal_moves(
    replay: Replay, lantern: Bank, console: Console
) -> None:
    lantern.set_faults(unknown_modal_on="/member")
    task = start(replay, lantern, {"member_id": DEMO}, options=ESCALATE, desk=console.desk)
    iv = await console.op.wait_for_intervention("takeover")
    run_id = iv["run_id"]
    http = console.op.http
    bad = await http.post(f"/api/sessions/{run_id}/take", json={"operator": "Not Valid"})
    assert bad.status_code == 422
    early = await http.post(f"/api/sessions/{run_id}/resume", json={"operator": "scripted"})
    assert early.status_code == 409  # nobody has taken control yet
    wrong = await http.post(f"/api/interventions/{iv['id']}/approve", json={"operator": "scripted"})
    assert wrong.status_code == 409  # approve applies to approvals only
    page = await http.get(f"/sessions/{run_id}")
    assert page.is_success and run_id in page.text
    assert (await http.get("/")).is_success
    await console.op.abort(run_id)
    await task


async def test_a_busy_console_port_fails_clearly() -> None:
    from fastapi import FastAPI

    async with console_server(FastAPI(), port=0) as url:
        port = int(url.rsplit(":", 1)[1])
        with pytest.raises(ConsolePortInUse, match="--console-port"):
            async with console_server(FastAPI(), port=port):
                pass
