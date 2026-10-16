"""The discovery loop offline: a scripted fake model drives the real mock bank through the real
SDK client, so batches, tool-result shapes, recording and stop conditions are tested for free."""

import asyncio
import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Browser

from eagleview.artifacts.yaml_io import load_app_profile
from eagleview.control.interventions import InterventionDesk, InterventionStore
from eagleview.discovery import agent_loop
from eagleview.discovery.agent_loop import DiscoveryConfig, DiscoveryResult, discover
from eagleview.discovery.meter import Limits, MemoryMeter
from eagleview.discovery.tools import HALT_TEXT
from tests.discovery.fake_model import (
    MODEL,
    FakeModel,
    Turn,
    centre,
    custom,
    member,
    thinking,
)
from tests.fixtures import APP_PROFILE
from tests.support.bank import Bank
from tests.support.replay import policy_for, tenant_for

GOAL = "Look up member 10482 and read their share balances."
BALANCE = "$3,019.36"  # member 10482's S00 balance as the screen prints it (pinned seed data)
INPUTS = [
    {
        "name": "member_id",
        "type": "string",
        "value": "10482",
        "description": "Member number",
        "sensitivity": "identifier",
    }
]


async def open_inquiry(s: Any) -> list[dict[str, Any]]:
    return [
        thinking("Open Member Inquiry from the menu."),
        member("left_click"),  # no coordinate: fails, so the screenshot after it is halted
        member("screenshot"),
    ]


async def click_menu(s: Any) -> list[dict[str, Any]]:
    return [
        member("left_click", coordinate=await centre(s, "nav", "a:has-text('Member Inquiry')")),
        member("screenshot"),
    ]


async def search(s: Any) -> list[dict[str, Any]]:
    return [
        member("left_click", coordinate=await centre(s, "main", "input[name='txtA']")),
        custom("fill_input", param="member_id"),
        member("left_click", coordinate=await centre(s, "main", "img[src*='go']")),
    ]


async def finish(s: Any) -> list[dict[str, Any]]:
    name = await centre(
        s, "main", "xpath=//td[.//b[normalize-space()='Name:']]/following-sibling::td[1]"
    )
    cell = await centre(s, "main", "font:text-is('S00') >> nth=0")
    heading = await centre(s, "main", "b:text-is('Member Detail')")
    return [
        custom("mark_output", name="member_name", kind="value", coordinate=name, description="x"),
        custom(
            "mark_output", name="share_accounts", kind="table", coordinate=cell, description="y"
        ),
        custom(
            "declare_success",
            coordinate=heading,
            description=f"Member Detail shows S00 {BALANCE} and the rest.",
        ),
    ]


async def run(
    browser: Browser,
    bank: Bank,
    tmp_path: Path,
    turns: list[Turn],
    *,
    config: dict[str, Any] | None = None,
    meter: MemoryMeter | None = None,
    desk: InterventionDesk | None = None,
) -> tuple[DiscoveryResult, FakeModel, Path]:
    desk = desk or new_desk(tmp_path)
    fake = FakeModel(desk, INPUTS, turns)
    result = await discover(
        DiscoveryConfig(goal=GOAL, model=MODEL, **(config or {})),
        profile=load_app_profile(APP_PROFILE),
        tenant=tenant_for(bank),
        policy=policy_for(bank),
        browser=browser,
        meter=meter or MemoryMeter(),
        environ={"ANTHROPIC_API_KEY": "sk-fake-key", bank.tenant.password_env: bank.password},
        runs_root=tmp_path / "runs",
        desk=desk,
        http_client=fake.client(),
    )
    assert not fake.errors, fake.errors
    return result, fake, tmp_path / "runs" / result.run_id


def new_desk(tmp_path: Path) -> InterventionDesk:
    return InterventionDesk(
        InterventionStore(tmp_path / "iv.sqlite"), human_timeout_s=1.0, notify=lambda _l: None
    )


def trace(run_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in (run_dir / "trace.jsonl").read_text().splitlines()]


def results_of(request: dict[str, Any]) -> list[dict[str, Any]]:
    return [b for b in request["messages"][-1]["content"] if b["type"] == "tool_result"]


async def test_a_scripted_discovery_records_a_compilable_trace(
    browser: Browser, lantern: Bank, tmp_path: Path
) -> None:
    result, fake, run_dir = await run(
        browser, lantern, tmp_path, [open_inquiry, click_menu, search, finish]
    )
    assert result.status == "success", result
    assert result.stop_reason == "declared_success"
    assert result.outputs == ["member_name", "share_accounts"]
    assert result.inputs["member_id"].type == "string"
    assert result.actions == 4  # failed click, menu click, field click, fill, GO: counted ones
    assert result.usd > Decimal(0)

    first, turn2 = fake.requests[1], fake.requests[2]
    # The request: computer toolset with the denied members off, plus the four custom tools.
    toolset = first["tools"][0]
    assert toolset["type"] == "computer_toolset_20260801"
    assert toolset["configs"]["left_click_drag"] == {"enabled": False}
    assert [t["name"] for t in first["tools"][1:]] == [
        "fill_input",
        "mark_output",
        "declare_success",
        "request_human",
    ]
    assert first["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert first["output_config"] == {"effort": "medium"}
    assert first["fallbacks"] == "default"
    # Instruction text before the image in the first user turn.
    kinds = [b["type"] for b in first["messages"][0]["content"]]
    assert kinds == ["text", "image"]

    # A failed member, then the exact halt text for the rest of the batch, both echoing the toolset.
    failed, halted = results_of(turn2)
    assert failed["is_error"] and failed["toolset_name"] == "computer"
    assert halted == {
        "type": "tool_result",
        "tool_use_id": halted["tool_use_id"],
        "content": HALT_TEXT,
        "toolset_name": "computer",
        "is_error": True,
    }
    # History is append-only: the assistant turn comes back verbatim.
    assert turn2["messages"][1]["content"][0]["type"] == "thinking"

    # Custom tool results never carry toolset_name; a batch not ending in a screenshot gets one.
    later = results_of(fake.requests[4])
    click_field, fill, click_go = later
    assert "toolset_name" not in fill and fill["content"] == "OK"
    assert click_field["toolset_name"] == "computer"
    assert click_go["content"][-1]["type"] == "image"

    steps = trace(run_dir)
    tools = [s["tool"] for s in steps]
    assert tools == [
        "left_click",
        "left_click",
        "screenshot",  # looking is recorded too; the compiler drops it
        "left_click",
        "fill_input",
        "left_click",
        "mark_output",
        "mark_output",
        "declare_success",
    ]
    menu = steps[1]
    assert menu["status"] == "done" and menu["changed"]
    assert {c["strategy"] for c in menu["grounded"]["candidates"]} >= {"a11y", "text"}
    assert steps[0]["rationale"] == "Open Member Inquiry from the menu."
    assert steps[4]["bound"] == "member_id"
    table = steps[7]["table"]
    assert table["caption"] == "Share Accounts" and table["headers"][0] == "Sfx"
    assert (run_dir / steps[1]["shots"]["after"]).is_file()
    # The member number never reaches disk readable.
    written = "".join(p.read_text() for p in run_dir.rglob("*.jsonl"))
    assert "10482" not in written and "sk-fake-key" not in written
    # The model quoted a balance it had marked; the default policy masks amounts.
    assert "3,019.36" not in written and "[marked_amount]" in written


async def test_typing_a_goal_value_with_type_is_auto_bound(
    browser: Browser, lantern: Bank, tmp_path: Path
) -> None:
    async def typed(s: Any) -> list[dict[str, Any]]:
        return [
            member("left_click", coordinate=await centre(s, "main", "input[name='txtA']")),
            member("type", text="10482"),
        ]

    _, _, run_dir = await run(browser, lantern, tmp_path, [click_menu, typed])
    steps = trace(run_dir)
    assert steps[-1]["tool"] == "type" and steps[-1]["bound"] == "member_id"


@pytest.mark.parametrize(
    ("turns", "config", "status", "reason"),
    [
        ([], {}, "failed", "ended"),
        (  # a stall: handed to a person, who doesn't answer within the test's 1 s
            [lambda s: _same([member("mouse_move", coordinate=[700, 700])] * 4)],
            {},
            "needs_human",
            "no_progress",
        ),
        (
            [lambda s: _same([member("key", text="ctrl+w")])] * 2,
            {},
            "failed",
            "policy_blocks",
        ),
        ([click_menu, search], {"max_actions": 3}, "needs_human", "action_limit"),  # a stall
        (
            [lambda s: _same([custom("request_human", reason="stuck")])],
            {},
            "needs_human",
            "no_human",
        ),
    ],
    ids=["ended", "no_progress", "policy_blocks", "action_limit", "request_human"],
)
async def test_stop_conditions(
    browser: Browser,
    lantern: Bank,
    tmp_path: Path,
    turns: list[Turn],
    config: dict[str, Any],
    status: str,
    reason: str,
) -> None:
    result, _, _ = await run(browser, lantern, tmp_path, turns, config=config)
    assert (result.status, result.stop_reason) == (status, reason)


async def test_the_spend_limit_stops_before_the_next_request(
    browser: Browser, lantern: Bank, tmp_path: Path
) -> None:
    meter = MemoryMeter(Limits(max_usd=Decimal("0.000001")))
    result, fake, _ = await run(browser, lantern, tmp_path, [click_menu], meter=meter)
    assert (result.status, result.stop_reason) == ("budget_stopped", "budget")
    assert len(fake.requests) == 1  # input extraction spent the budget; no turn was sent


async def _same(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for b in blocks:
        copy = dict(b)
        copy["id"] = f"{b['id']}_{len(out)}"
        out.append(copy)
    return out


# --- the clock and stalls -------------------------------------------------------------------------


class Clock:
    """The discovery clock, moved only by hand, so a slow machine can't move it past a limit."""

    def __init__(self) -> None:
        self.offset = 0.0

    def __call__(self) -> float:
        return self.offset


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(agent_loop, "_clock", c)
    return c


def after(clock: Clock, seconds: float, turn: Turn) -> Turn:
    """A turn that plays once `seconds` have passed on the discovery clock."""

    async def go(s: Any) -> list[dict[str, Any]]:
        clock.offset += seconds
        return await turn(s)

    return go


def ask_for_help(s: Any) -> Any:
    return _same([custom("request_human", reason="Please check the member detail page.")])


async def test_the_limit_is_600_s_and_just_under_it_discovery_carries_on(
    browser: Browser, lantern: Bank, tmp_path: Path, clock: Clock
) -> None:
    assert DiscoveryConfig(goal=GOAL, model=MODEL).max_seconds == 600
    turns = [click_menu, after(clock, 599, search), finish]
    result, _, _ = await run(browser, lantern, tmp_path, turns)
    assert (result.status, result.stop_reason) == ("success", "declared_success")


async def test_past_600_s_a_stalled_discovery_goes_to_a_person(
    browser: Browser, lantern: Bank, tmp_path: Path, clock: Clock
) -> None:
    result, fake, run_dir = await run(
        browser, lantern, tmp_path, [after(clock, 601, click_menu), search, finish]
    )
    assert (result.status, result.stop_reason) == ("needs_human", "time_limit")
    assert "600 s without success" in (result.message or "")
    assert "nobody answered" in (result.message or "")
    turns = [r for r in fake.requests if "format" not in r.get("output_config", {})]
    assert len(turns) == 1  # the model was not asked again once time was up
    [iv] = fake.desk.store.list(run_id=result.run_id)
    assert (iv.kind, iv.reason, iv.goal) == ("takeover", "DISCOVERY_STALLED", GOAL)
    assert iv.resolution == "timed_out"
    assert iv.screenshot is not None and (run_dir / iv.screenshot).is_file()
    kept = json.loads((run_dir / f"intervention_{iv.id.lower()}.json").read_text())
    assert (kept["reason"], kept["resolution"]) == ("DISCOVERY_STALLED", "timed_out")
    assert kept["state_summary"] and kept["screenshot"] == iv.screenshot
    stalled = [
        json.loads(x)
        for x in (run_dir / "run.jsonl").read_text().splitlines()
        if '"discovery.stalled"' in x
    ]
    assert [e["stop_reason"] for e in stalled] == ["time_limit"]


async def answer(desk: InterventionDesk, then: Callable[[], None] | None = None) -> None:
    """A person: wait for the request, take the session, and hand it straight back."""
    asked = asyncio.Event()
    desk.notify = lambda _line: asked.set()
    await asked.wait()
    [iv] = desk.store.list(open_only=True)
    if then is not None:
        then()
    ctl = desk.session(iv.run_id).controller
    ctl.take_control("operator:test")
    ctl.resume("operator:test")
    desk.resolve(iv.id, "resumed", "operator:test", "looked; carry on")


async def test_time_a_person_holds_the_session_is_not_counted(
    browser: Browser, lantern: Bank, tmp_path: Path, clock: Clock
) -> None:
    desk = new_desk(tmp_path)

    def fifteen_minutes() -> None:
        clock.offset += 900  # longer than the whole limit

    person = asyncio.create_task(answer(desk, fifteen_minutes))
    result, _, _ = await run(
        browser, lantern, tmp_path, [click_menu, search, ask_for_help, finish], desk=desk
    )
    await person
    assert (result.status, result.stop_reason) == ("success", "declared_success")


async def test_a_stall_a_person_answers_still_ends_needing_a_person(
    browser: Browser, lantern: Bank, tmp_path: Path
) -> None:
    """The person sees the live screen and may finish the step by hand; the model is not asked
    again, because the limits are cost guards."""
    desk = new_desk(tmp_path)
    person = asyncio.create_task(answer(desk))
    still = [lambda s: _same([member("mouse_move", coordinate=[700, 700])] * 4), finish]
    result, fake, _ = await run(browser, lantern, tmp_path, still, desk=desk)
    await person
    assert (result.status, result.stop_reason) == ("needs_human", "no_progress")
    assert "a person handed back" in (result.message or "")
    [iv] = desk.store.list(run_id=result.run_id)
    assert (iv.reason, iv.resolution) == ("DISCOVERY_STALLED", "resumed")
    turns = [r for r in fake.requests if "format" not in r.get("output_config", {})]
    assert len(turns) == 1
