"""Surface.act(): the choke point.

(a) Grounding happens immediately before the action, at the exact point, and a target that
    changed since resolution is refused, not clicked.
(b) Fill replays as keystrokes, so per-keystroke handlers run.
Plus: policy sees the grounded label (pointer and keyboard), the lease holder is checked first,
and observe/zoom produce in-memory images at the pinned geometry."""

import asyncio
import io
from dataclasses import replace
from typing import Any

import pytest
from PIL import Image

from eagleview.safety.policy_engine import ApprovalToken
from eagleview.schema.action import Action
from eagleview.surfaces.web.playwright_surface import PlaywrightSurface, playwright_key
from tests.support.bank import Bank
from tests.surfaces.conftest import RUN_ID, Events
from tests.surfaces.helpers import (
    EPOCH,
    GO_IMAGE,
    MEMBER_FIELD,
    NAV_INQUIRY,
    act_on,
    button,
    pred,
    resolved,
    spec,
    to_inquiry,
    to_member,
    wait_until,
)

NUM_FIELD = spec(
    {"strategy": "anchor", "anchor_text": "Member #", "relation": "right_of", "control": "textbox"},
    {"strategy": "css", "selector": "input[name='num']"},
)
REF_FIELD = spec(
    {
        "strategy": "anchor",
        "anchor_text": "Reference",
        "relation": "right_of",
        "control": "textbox",
    },
    {"strategy": "css", "selector": "input[name='ref']"},
)


# --- (a) grounding immediately before acting -------------------------------------------------


@pytest.mark.parametrize("record_candidates", [False, True])
async def test_grounding_runs_right_before_the_mouse_at_the_same_point(
    open_surface: Any, lantern: Bank, record_candidates: bool
) -> None:
    """Ground, then (while recording) generate candidates, then a last hit-test confirmation,
    then the mouse: nothing reads the page between the confirmation and the click."""
    surface = await open_surface(lantern)
    await to_inquiry(surface)
    surface.record_candidates = record_candidates
    calls: list[str] = []
    points: list[tuple[int, int]] = []
    grounder, resolver, mouse = surface.grounder, surface.resolver, surface.page.mouse
    real_ground, real_confirm, real_find, real_click = (
        grounder.at_point,
        grounder.confirm,
        resolver.find,
        mouse.click,
    )

    async def ground(x: int, y: int) -> Any:
        calls.append("ground")
        points.append((x, y))
        return await real_ground(x, y)

    async def confirm(g: Any) -> bool:
        calls.append("confirm")
        return await real_confirm(g)

    async def find(*args: Any) -> Any:
        calls.append("find")
        return await real_find(*args)

    async def click(x: float, y: float, **kw: Any) -> None:
        calls.append("click")
        points.append((int(x), int(y)))
        await real_click(x, y, **kw)

    grounder.at_point = ground  # type: ignore[method-assign]
    grounder.confirm = confirm  # type: ignore[method-assign]
    resolver.find = find  # type: ignore[method-assign]
    mouse.click = click  # type: ignore[method-assign]
    target = await resolved(surface, GO_IMAGE)
    calls.clear()
    result = await surface.act(Action(type="click"), "automation", EPOCH, target=target)
    assert result.ok
    assert calls[0] == "ground" and calls[-2:] == ["confirm", "click"]
    assert ("find" in calls) is record_candidates
    assert points[0] == points[-1] == result.point
    assert result.grounded is not None and result.grounded.tag == "img"
    assert bool(result.grounded.candidates) is record_candidates


async def test_a_modal_appearing_after_resolution_blocks_the_click(
    open_surface: Any, lantern: Bank
) -> None:
    surface = await open_surface(lantern)
    await to_member(surface, lantern, lantern.demo)
    open_share = await resolved(surface, button("Open Share"))
    assert not open_share.occluded
    # The page changes between resolve and act: a fake modal covers the main frame.
    main = surface.page.frame(name="main")
    assert main is not None
    await main.evaluate(
        "() => { const d = document.createElement('div');"
        " d.style.cssText = 'position:fixed;left:0;top:0;width:100%;height:100%;z-index:999;"
        "background:rgba(0,0,0,.3)'; document.body.appendChild(d); }"
    )
    result = await surface.act(Action(type="click"), "automation", EPOCH, target=open_share)
    assert result.status == "target_mismatch"
    assert result.grounded is not None and result.grounded.tag == "div"
    await asyncio.sleep(0.3)
    # Still on Member Detail: the button underneath was never clicked. (Asked through the
    # surface, "Member Detail" is not present any more: the overlay covers it.)
    assert main.url.endswith("/member")
    assert not await surface.check(_text("Member Detail"))


async def test_a_navigation_after_resolution_blocks_the_action(
    open_surface: Any, lantern: Bank
) -> None:
    surface = await open_surface(lantern)
    await to_inquiry(surface)
    field = await resolved(surface, MEMBER_FIELD)
    main = surface.page.frame(name="main")
    assert main is not None
    await main.evaluate("() => { location.href = '/home'; }")
    await wait_until(surface, {"text_present": {"frame": "main", "text": "Teller Home"}})
    result = await surface.act(Action(type="fill", text="10482"), "automation", EPOCH, target=field)
    assert result.status == "target_mismatch"


async def test_off_screen_targets_are_scrolled_into_view_then_hit_tested(
    open_surface: Any, lantern: Bank
) -> None:
    surface = await open_surface(lantern)
    await to_member(surface, lantern, "10014")  # four shares, two loans: taller than the frame
    main = surface.page.frame(name="main")
    assert main is not None
    open_share = await resolved(surface, button("Open Share"))
    # resolve() scrolled it into view to hit-test it; scroll back so act() has to do it too.
    await main.evaluate("() => window.scrollTo(0, 0)")
    before = await surface.resolver.point_for(open_share.element, scroll=False)
    assert not before["topmost"], "precondition: the button starts below the fold"
    result = await surface.act(Action(type="click"), "automation", EPOCH, target=open_share)
    assert result.ok, result
    assert result.point is not None and result.point[1] < 800
    await wait_until(surface, {"text_present": {"frame": "main", "text": "Open Share Account"}})


# --- (b) fill types keystrokes ---------------------------------------------------------------


async def test_fill_fires_every_keystroke_handler(open_site: Any) -> None:
    surface: PlaywrightSurface = await open_site("keystrokes.html")
    result = await act_on(surface, NUM_FIELD, "fill", text="10A82")
    assert result.ok, result
    doc = surface.page.main_frame
    # keypress refused the letter, as it would for a person; keydown and keyup saw all five keys.
    assert await doc.input_value("input[name='num']") == "1082"
    assert await doc.inner_text("span") == "5/5"
    assert await doc.is_enabled("input[name='go']")  # enabled by the keyup handler


async def test_setting_the_value_would_have_skipped_the_handlers(open_site: Any) -> None:
    """Why fill types: Playwright's own fill() sets the value, and the legacy form never knows."""
    surface: PlaywrightSurface = await open_site("keystrokes.html")
    await surface.page.main_frame.fill("input[name='num']", "10A82")
    doc = surface.page.main_frame
    assert await doc.input_value("input[name='num']") == "10A82"
    assert await doc.inner_text("span") == "0/0"
    assert not await doc.is_enabled("input[name='go']")


async def test_fill_replaces_existing_text(open_site: Any) -> None:
    surface: PlaywrightSurface = await open_site("keystrokes.html")
    assert (await act_on(surface, REF_FIELD, "fill", text="NEW-1")).ok
    assert await surface.page.main_frame.input_value("input[name='ref']") == "NEW-1"
    assert await surface.check(_field(REF_FIELD, "NEW-1"))


async def test_fill_refuses_text_it_cannot_type_as_keys(open_site: Any) -> None:
    surface: PlaywrightSurface = await open_site("keystrokes.html")
    result = await act_on(surface, REF_FIELD, "fill", text="José")
    assert result.status == "error"
    assert await surface.page.main_frame.input_value("input[name='ref']") == "OLD-VALUE"


async def test_fill_on_the_bank_then_field_value(open_surface: Any, lantern: Bank) -> None:
    surface = await open_surface(lantern)
    await to_inquiry(surface)
    assert (await act_on(surface, MEMBER_FIELD, "fill", text="99999")).ok
    assert (await act_on(surface, MEMBER_FIELD, "fill", text=lantern.demo)).ok
    assert await surface.check(_field(MEMBER_FIELD, lantern.demo))
    assert not await surface.check(_field(MEMBER_FIELD, "99999"))


async def test_fill_needs_a_text_field(open_surface: Any, lantern: Bank) -> None:
    surface = await open_surface(lantern)
    await to_inquiry(surface)
    result = await act_on(surface, GO_IMAGE, "fill", text="1")
    assert result.status == "error" and "not a text field" in result.reason


# --- policy sees what is really there --------------------------------------------------------


async def _to_review(surface: PlaywrightSurface, bank: Bank, actor: str = "automation") -> None:
    async def act(target: Any, action: str = "click", **fields: Any) -> None:
        result = await act_on(surface, target, action, actor=actor, **fields)
        assert result.ok, result

    async def until(text: str) -> None:
        await wait_until(surface, {"text_present": {"frame": "main", "text": text}})

    await act(NAV_INQUIRY)
    await until("Member Search")
    await act(MEMBER_FIELD, "fill", text=bank.demo)
    await act(GO_IMAGE)
    await until("Member Detail")
    await act(button("Open Share"))
    await until("Open Share Account")
    share_type = spec(
        {
            "strategy": "anchor",
            "anchor_text": "Share Type",
            "relation": "right_of",
            "control": "select",
        },
        frame=["main"],
    )
    await act(share_type, "select", option=bank.tenant.share_types["money_market"].description)
    await act(button("Validate"))
    await until("Review - Open Share")


async def test_post_needs_an_approval_token_for_its_step(open_surface: Any, lantern: Bank) -> None:
    surface = await open_surface(lantern)
    await _to_review(surface, lantern)
    result = await act_on(surface, button("Post"), step_id="post_share")
    assert result.status == "needs_approval"
    assert result.decision is not None and result.decision.irreversible
    assert result.grounded is not None and result.grounded.name == "Post"
    assert await surface.check(_text("Review - Open Share"))

    # A token for another step authorizes nothing.
    other = ApprovalToken(RUN_ID, "other_step")
    surface.policy_ctx = replace(surface.policy_ctx, approvals=frozenset({other}))
    assert (await act_on(surface, button("Post"), step_id="post_share")).status == "needs_approval"

    mine = ApprovalToken(RUN_ID, "post_share")
    surface.policy_ctx = replace(surface.policy_ctx, approvals=frozenset({mine}))
    assert (await act_on(surface, button("Post"), step_id="post_share")).ok
    await wait_until(surface, {"text_present": {"frame": "main", "text": "Share Account Opened"}})


async def test_enter_on_a_focused_post_button_is_seen_as_post(
    open_surface: Any, lantern: Bank
) -> None:
    surface = await open_surface(lantern)
    await _to_review(surface, lantern)
    main = surface.page.frame(name="main")
    assert main is not None
    await main.focus("input[value='Post']")
    result = await surface.act(Action(type="key", key="Return"), "automation", EPOCH)
    assert result.status == "needs_approval"
    assert result.grounded is not None and result.grounded.name == "Post"


async def test_discovery_escalates_irreversible_clicks(open_surface: Any, lantern: Bank) -> None:
    surface = await open_surface(lantern, actor="llm", mode="discovery")
    await _to_review(surface, lantern, actor="llm")
    # The model clicks by pixel, with no resolved target: grounding alone names it "Post".
    post = await resolved(surface, button("Post"))
    p = await surface.resolver.point_for(post.element, scroll=True)
    result = await surface.act(Action(type="click", x=p["x"], y=p["y"]), "llm", EPOCH)
    assert result.status == "escalate"
    assert result.grounded is not None and result.grounded.name == "Post"


# --- the lease holder is checked first --------------------------------------------------------


async def test_only_the_holder_at_its_epoch_may_act(
    open_surface: Any, lantern: Bank, events: Events
) -> None:
    surface = await open_surface(lantern)
    link = await resolved(surface, spec({"strategy": "text", "text": "Member Inquiry"}))
    for actor, epoch in (("operator:dana", EPOCH), ("automation", EPOCH + 1)):
        result = await surface.act(Action(type="click"), actor, epoch, target=link)
        assert result.status == "not_holder"
        assert result.grounded is None  # refused before touching the page
    assert not await surface.check(_text("Member Search"))
    assert [e["status"] for e in events.of("surface.act")] == ["not_holder", "not_holder"]


# --- observe and zoom -------------------------------------------------------------------------


async def test_observe_is_a_pinned_size_screenshot_with_a_stable_fingerprint(
    open_surface: Any, lantern: Bank
) -> None:
    surface = await open_surface(lantern)
    first = await surface.observe()
    with Image.open(io.BytesIO(first.screenshot)) as img:
        assert img.size == (1280, 800)
    assert first.headings == ("Teller Home",)
    await asyncio.sleep(1.2)  # the banner's session countdown ticks
    assert (await surface.observe()).fingerprint == first.fingerprint
    await to_inquiry(surface)
    assert (await surface.observe()).fingerprint != first.fingerprint


async def test_zoom_returns_the_clipped_region(open_surface: Any, lantern: Bank) -> None:
    surface = await open_surface(lantern)
    result = await surface.act(Action(type="zoom", region=(0, 0, 200, 56)), "automation", EPOCH)
    assert result.ok and result.image is not None
    with Image.open(io.BytesIO(result.image)) as img:
        assert img.size == (200, 56)


def test_key_names_map_to_playwright() -> None:
    assert playwright_key("Return") == "Enter"
    assert playwright_key("shift+Tab") == "Shift+Tab"
    assert playwright_key("ctrl+a") == "Control+a"
    assert playwright_key("Page_Down") == "PageDown"
    assert playwright_key("BackSpace") == "Backspace"


def _text(text: str) -> Any:
    return pred({"text_present": {"frame": "main", "text": text}})


def _field(target: Any, value: str) -> Any:
    return pred({"field_value": {"target": target.model_dump(mode="json"), "equals": value}})


async def test_wait_is_only_for_the_discovery_model(open_surface: Any, lantern: Bank) -> None:
    """Replay never sleeps (it polls predicates); the surface enforces that, not convention."""
    surface = await open_surface(lantern)
    result = await surface.act(Action(type="wait", duration_ms=10), "automation", EPOCH)
    assert result.status == "error"


async def test_act_events_name_only_links_and_buttons(
    open_surface: Any, lantern: Bank, events: Events
) -> None:
    surface = await open_surface(lantern)
    await to_member(surface, lantern, lantern.demo)
    name_cell = spec(
        {"strategy": "anchor", "anchor_text": "Name:", "relation": "right_of", "control": "text"},
        frame=["main"],
    )
    assert (await act_on(surface, name_cell)).ok
    logged = events.of("surface.act")[-1]["target"]
    assert logged["text"] is None and logged["name"] is None
    assert any(
        e["target"] and e["target"]["text"] == "Member Inquiry" for e in events.of("surface.act")
    )
