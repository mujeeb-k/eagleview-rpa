"""The write flow through Post, compiled end to end, offline.

Discovery never commits: when the fake model reaches Post, discovery hands the session to a
person. A SCRIPTED operator on the console's HTTP API clicks Post and hands back. The compiler
turns that recorded click into an irreversible step that needs an approval, and the verify replay
asks the console for one, which the scripted operator gives."""

import asyncio
from pathlib import Path
from typing import Any

from playwright.async_api import Browser

from eagleview.artifacts.yaml_io import load_app_profile, load_capability
from eagleview.compiler.pipeline import discover_and_compile
from eagleview.control.interventions import InterventionDesk, InterventionStore
from eagleview.discovery.agent_loop import DiscoveryConfig
from eagleview.discovery.meter import MemoryMeter
from eagleview.operator.console import create_console
from eagleview.operator.server import console_server
from tests.discovery.fake_model import MODEL, FakeModel, centre, custom, member
from tests.discovery.test_agent_loop import click_menu, search
from tests.fixtures import APP_PROFILE
from tests.support.bank import Bank
from tests.support.operator import ScriptedOperator
from tests.support.replay import policy_for, share_rows, tenant_for

GOAL = "Open a Money Market share account for member 10482 and post it."
INPUTS = [
    {
        "name": "member_id",
        "type": "string",
        "value": "10482",
        "description": "Member number",
        "sensitivity": "identifier",
    },
    {
        "name": "share_type",
        "type": "string",
        "value": "Money Market",
        "description": "Share type",
        "sensitivity": "none",
    },
]


async def open_share(s: Any) -> list[dict[str, Any]]:
    return [member("left_click", coordinate=await centre(s, "main", "input[value='Open Share']"))]


async def choose(s: Any) -> list[dict[str, Any]]:
    return [
        member("left_click", coordinate=await centre(s, "main", "select[name='f3']")),
        custom("fill_input", param="share_type"),
        member("left_click", coordinate=await centre(s, "main", "input[value='Validate']")),
    ]


async def post(s: Any) -> list[dict[str, Any]]:
    return [member("left_click", coordinate=await centre(s, "main", "input[value='Post']"))]


async def confirm(s: Any) -> list[dict[str, Any]]:
    txn = await centre(
        s, "main", "xpath=//td[.//b[normalize-space()='Transaction #:']]/following-sibling::td[1]"
    )
    heading = await centre(s, "main", "b:text-is('Share Account Opened')")
    return [
        custom(
            "mark_output",
            name="transaction_number",
            kind="value",
            coordinate=txn,
            description="The posting's transaction number",
        ),
        custom("declare_success", coordinate=heading, description="The share is opened"),
    ]


NAMES = {
    "capability_id": "member.open_share",
    "title": "Open a share account",
    "description": "Opens and posts a new share account for a member.",
    "steps": [
        {"index": i, "id": sid, "intent": sid.replace("_", " "), "risk": risk}
        for i, (sid, risk) in enumerate(
            [
                ("open_member_inquiry", "read"),
                ("enter_member_id", "read"),
                ("submit_search", "read"),
                ("open_share_form", "read"),
                ("choose_share_type", "read"),
                ("validate_share", "write_staged"),
                ("post_share", "read"),  # a proposal can never lower what the policy recorded
                ("read_confirmation", "read"),
            ]
        )
    ],
    "outputs": [],
    "inputs": [
        {
            "name": "member_id",
            "description": "Member number.",
            "pattern": "^[0-9]{4,10}$",
            "example": "10482",
        },
        {
            "name": "share_type",
            "description": "Share type label.",
            "pattern": "^.{1,40}$",
            "example": "Money Market",
        },
    ],
}


async def operator_loop(op: ScriptedOperator, stop: asyncio.Event) -> list[str]:
    """Answer whatever the console asks, the way a person at the console would."""
    done: list[str] = []
    while not stop.is_set():
        for iv in (await op.http.get("/api/interventions", params={"open": True})).json():
            if iv["kind"] == "takeover" and iv["reason"] == "APPROVAL_REQUIRED":
                await op.take(iv["run_id"])
                await op.click_text(iv["run_id"], "Post")
                await op.resume(iv["run_id"], note="Checked the review screen; posted.")
                done.append("posted")
            elif iv["kind"] == "approval":
                await op.approve(iv["id"], note="Verify replay on the training tenant.")
                done.append("approved")
        await asyncio.sleep(0.1)
    return done


async def test_a_post_flow_compiles_with_an_approval_gate(
    browser: Browser, lantern: Bank, tmp_path: Path
) -> None:
    before = share_rows(lantern, "10482")
    desk = InterventionDesk(
        InterventionStore(tmp_path / "iv.sqlite"), human_timeout_s=30, notify=lambda _l: None
    )
    fake = FakeModel(desk, INPUTS, [click_menu, search, open_share, choose, post, confirm])
    fake.structured = {"capability_id": NAMES}
    async with console_server(create_console(desk, tmp_path / "runs"), port=0) as url:
        desk.console_url = url
        op = ScriptedOperator(url)
        stop = asyncio.Event()
        answering = asyncio.create_task(operator_loop(op, stop))
        result = await discover_and_compile(
            DiscoveryConfig(goal=GOAL, model=MODEL),
            profile=load_app_profile(APP_PROFILE),
            tenant=tenant_for(lantern),
            policy=policy_for(lantern),
            browser=browser,
            meter=MemoryMeter(),
            environ={"ANTHROPIC_API_KEY": "sk-fake", lantern.tenant.password_env: lantern.password},
            runs_root=tmp_path / "runs",
            drafts_root=tmp_path / "drafts",
            desk=desk,
            http_client=fake.client(),
        )
        stop.set()
        actions = await answering
        await op.close()
    assert not fake.errors, fake.errors
    assert result.status == "verified", result.review()
    assert actions == ["posted", "approved"]
    assert result.path is not None
    cap = load_capability(result.path)
    ids = [s.id for s in cap.steps]
    assert ids == [s["id"] for s in NAMES["steps"]]
    choose_step = cap.steps[4]
    assert choose_step.action.type == "select"
    assert choose_step.action.option == "{{ inputs.share_type }}"
    validate, post_step = cap.steps[5], cap.steps[6]
    assert validate.risk == "write_staged"
    assert (post_step.risk, post_step.requires_approval, post_step.idempotent) == (
        "irreversible",
        True,
        False,
    )
    assert cap.risk == "irreversible" and cap.outcomes == []
    assert set(result.replays) == {"verify"}  # never probed, never posted a third time
    # One share from the person during discovery, one from the approved verify replay.
    assert share_rows(lantern, "10482") == before + 2
