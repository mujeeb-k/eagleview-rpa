"""P3's done-when: every control in both flows on tenant A resolves through at least two strategies,
at least one of them semantic (a11y, anchor or text, not just structural css/xpath), and they all
land on the same element. Ambiguity is detected and never resolved by picking the first match."""

from typing import Any

import pytest

from eagleview.schema.locators import TargetSpec
from eagleview.surfaces.base import Ambiguous, Element, NotFound, Unique
from eagleview.surfaces.web.js import cdp_fn, element_fn
from eagleview.surfaces.web.playwright_surface import PlaywrightSurface
from tests.support.bank import Bank
from tests.surfaces.conftest import Events
from tests.surfaces.helpers import (
    GO_IMAGE,
    MEMBER_FIELD,
    NAV_INQUIRY,
    act_on,
    button,
    resolved,
    spec,
    to_inquiry,
    to_member,
    wait_until,
)

SEMANTIC = {"a11y", "anchor", "text"}


async def assert_every_strategy_agrees(surface: PlaywrightSurface, target: TargetSpec) -> None:
    """Each locator on its own is unique, and all of them find the same element."""
    first: Unique | None = None
    for loc in target.locators:
        single = target.model_copy(update={"locators": [loc], "fragile": None})
        result = await resolved(surface, single)
        if first is None:
            first = result
        else:
            assert await surface.resolver.same(first.element, result.element), loc
    assert len(target.locators) >= 2
    assert {loc.strategy for loc in target.locators} & SEMANTIC


async def assert_grounding_round_trip(surface: PlaywrightSurface, target: TargetSpec) -> None:
    """Ground the control's centre: the candidates must include two strategies, one semantic,
    and each candidate must resolve back to the very element that was grounded."""
    unique = await resolved(surface, target)
    point = await surface.resolver.point_for(unique.element, scroll=True)
    grounded = await surface.ground(point["x"], point["y"])
    strategies = {c.strategy for c in grounded.candidates}
    assert len(strategies - {"point"}) >= 2, grounded.candidates
    assert strategies & SEMANTIC, grounded.candidates
    for loc in grounded.candidates:
        again = await resolved(surface, spec(loc.model_dump(), frame=grounded.frame))
        assert await surface.resolver.same(again.element, unique.element), loc


FLOW_SEARCH: list[tuple[str, TargetSpec]] = [
    ("nav link", NAV_INQUIRY),
    ("member field", MEMBER_FIELD),
    ("GO image", GO_IMAGE),
]

OPEN_SHARE_FORM: list[tuple[str, TargetSpec]] = [
    (
        "share type select",
        spec(
            {
                "strategy": "anchor",
                "anchor_text": "Share Type",
                "relation": "right_of",
                "control": "select",
            },
            {"strategy": "css", "selector": "select[name='f3']"},
            frame=["main"],
        ),
    ),
    (
        "nickname field",
        spec(
            {
                "strategy": "anchor",
                "anchor_text": "Nickname",
                "relation": "right_of",
                "control": "textbox",
            },
            {"strategy": "css", "selector": "input[name='f4']"},
            frame=["main"],
        ),
    ),
    (
        "quarterly radio",
        # The radio's own label is to its right; "Dividend Frequency" on the left is shared by
        # every radio in the row, so it would be ambiguous.
        spec(
            {
                "strategy": "anchor",
                "anchor_text": "Quarterly",
                "relation": "left_of",
                "control": "radio",
            },
            {"strategy": "css", "selector": "input[name='f5'][value='Quarterly']"},
            frame=["main"],
        ),
    ),
    ("validate button", button("Validate")),
    ("cancel button", button("Cancel")),
]


async def test_search_flow_controls(open_surface: Any, lantern: Bank) -> None:
    surface = await open_surface(lantern)
    await assert_every_strategy_agrees(surface, NAV_INQUIRY)
    await assert_grounding_round_trip(surface, NAV_INQUIRY)
    await to_inquiry(surface)
    for name, target in FLOW_SEARCH[1:]:
        await assert_every_strategy_agrees(surface, target)
        await assert_grounding_round_trip(surface, target)
        assert name


async def test_lookup_and_open_share_flow_controls(open_surface: Any, lantern: Bank) -> None:
    surface = await open_surface(lantern)
    await to_member(surface, lantern, lantern.demo)
    name_value = spec(
        {"strategy": "anchor", "anchor_text": "Name:", "relation": "right_of", "control": "text"},
        {"strategy": "xpath", "expression": "//tr[normalize-space(td[1])='Name:']/td[2]"},
        frame=["main"],
    )
    for target in (button("Open Share"), button("New Search"), name_value):
        await assert_every_strategy_agrees(surface, target)
    await assert_grounding_round_trip(surface, button("Open Share"))

    assert (await act_on(surface, button("Open Share"))).ok
    await wait_until(surface, {"text_present": {"frame": "main", "text": "Open Share Account"}})
    for _, target in OPEN_SHARE_FORM:
        await assert_every_strategy_agrees(surface, target)
        await assert_grounding_round_trip(surface, target)

    money_market = lantern.tenant.share_types["money_market"].description
    assert (await act_on(surface, OPEN_SHARE_FORM[0][1], "select", option=money_market)).ok
    assert (await act_on(surface, OPEN_SHARE_FORM[2][1])).ok
    assert (await act_on(surface, button("Validate"))).ok
    await wait_until(surface, {"text_present": {"frame": "main", "text": "Review - Open Share"}})
    for target in (button("Post"), button("Back")):
        await assert_every_strategy_agrees(surface, target)
        await assert_grounding_round_trip(surface, target)


async def test_css_matching_two_fields_is_ambiguous(open_surface: Any, lantern: Bank) -> None:
    surface = await open_surface(lantern)
    await to_inquiry(surface)
    result = await surface.resolve(
        spec({"strategy": "css", "selector": "input[type='text']"}, frame=["main"])
    )
    assert isinstance(result, Ambiguous)
    assert result.count == 2


async def test_a_header_shared_by_tables_is_ambiguous(open_surface: Any, lantern: Bank) -> None:
    surface = await open_surface(lantern)
    await to_member(surface, lantern, lantern.demo)
    # "Description" heads the shares, loans and activity tables.
    for loc in (
        {"strategy": "text", "text": "Description"},
        {
            "strategy": "anchor",
            "anchor_text": "Description",
            "relation": "below",
            "control": "text",
        },
    ):
        result = await surface.resolve(spec(loc, frame=["main"]))
        assert isinstance(result, Ambiguous), loc


async def test_ranked_resolution_skips_ambiguity_and_reports_agreement(
    open_surface: Any, lantern: Bank, events: Events
) -> None:
    surface = await open_surface(lantern)
    await to_inquiry(surface)
    ranked = spec(
        {"strategy": "css", "selector": "input[type='text']"},  # ambiguous: skipped, not picked
        {
            "strategy": "anchor",
            "anchor_text": "Member #",
            "relation": "right_of",
            "control": "textbox",
        },
        {"strategy": "css", "selector": "input[name='txtA']"},
        frame=["main"],
    )
    result = await resolved(surface, ranked)
    assert (result.rank, result.strategy) == (1, "anchor")
    assert result.agreement is True and result.agreement_strategy == "css"
    assert [a.outcome for a in result.attempts] == ["ambiguous", "unique", "unique"]

    disagree = spec(
        {
            "strategy": "anchor",
            "anchor_text": "Member #",
            "relation": "right_of",
            "control": "textbox",
        },
        {"strategy": "css", "selector": "input[name='txtB']"},
        frame=["main"],
    )
    result = await resolved(surface, disagree)
    assert result.agreement is False
    logged = events.of("locator.resolve")[-1]
    assert logged["result"] == "unique" and logged["agreement"] is False

    everything_ambiguous = spec({"strategy": "css", "selector": "input"}, frame=["main"])
    assert isinstance(await surface.resolve(everything_ambiguous), Ambiguous)
    nothing = spec({"strategy": "text", "text": "No Such Control"}, frame=["main"])
    assert isinstance(await surface.resolve(nothing), NotFound)


async def test_frame_scope_is_exact(open_surface: Any, lantern: Bank) -> None:
    surface = await open_surface(lantern)
    in_main = spec({"strategy": "text", "text": "Member Inquiry"}, frame=["main"])
    assert isinstance(await surface.resolve(in_main), NotFound)
    anywhere = spec({"strategy": "text", "text": "Member Inquiry"})
    assert isinstance(await surface.resolve(anywhere), Unique)


async def test_point_locator_only_on_its_own_fingerprint(open_surface: Any, lantern: Bank) -> None:
    surface = await open_surface(lantern, app_fingerprint="ironbridge-teller@7.2/lantern-bay")
    link = await resolved(surface, NAV_INQUIRY)
    p = await surface.resolver.point_for(link.element, scroll=False)
    here = {"strategy": "point", "x": p["x"], "y": p["y"]}
    bound = await surface.resolve(
        spec(here | {"bound_to_fingerprint": "ironbridge-teller@7.2/lantern-bay"})
    )
    assert isinstance(bound, Unique)
    assert await surface.resolver.same(bound.element, link.element)
    other = await surface.resolve(
        spec(here | {"bound_to_fingerprint": "ironbridge-teller@7.4/copperhill"})
    )
    assert isinstance(other, NotFound)
    assert other.attempts[0].outcome == "skipped"
    grounded = await surface.ground(p["x"], p["y"])
    assert grounded.candidates[-1].strategy == "point"


async def test_tenant_aliases_on_copperhill(open_surface: Any, copperhill: Bank) -> None:
    """The same Lantern Bay targets work on tenant B through its label aliases."""
    aliases = {"Member #": ["Acct Holder No."], "Member Inquiry": ["Member Lookup"]}
    surface = await open_surface(copperhill, label_aliases=aliases)
    await to_member(surface, copperhill, copperhill.demo)
    await wait_until(surface, {"text_present": {"frame": "main", "text": "Share Accounts"}})


@pytest.mark.parametrize("frame", ["nav", "main", "banner"])
async def test_js_frame_offsets_agree_with_the_browser_hit_test(
    open_surface: Any, lantern: Bank, frame: str
) -> None:
    """The JS frame-offset arithmetic (frameset borders included) must put each point exactly
    where the browser's own CDP hit test finds the element: the node CDP reports is the element
    or inside it, in the same frame."""
    surface = await open_surface(lantern)
    f = surface.page.frame(name=frame)
    assert f is not None
    checked = 0
    for h in await f.locator("a, input, img, b").element_handles():
        point = await surface.resolver.point_for(Element(h, (frame,), ""), scroll=False)
        if not point.get("topmost"):
            continue
        hit = await surface.cdp.send(
            "DOM.getNodeForLocation",
            {"x": point["x"], "y": point["y"], "ignorePointerEventsNone": True},
        )
        obj = await surface.cdp.send("DOM.resolveNode", {"backendNodeId": hit["backendNodeId"]})
        where = await surface.cdp.send(
            "Runtime.callFunctionOn",
            {
                "objectId": obj["object"]["objectId"],
                "functionDeclaration": cdp_fn(
                    "return [EV.framePath(el.ownerDocument.defaultView), EV.domKey(el)];"
                ),
                "returnByValue": True,
            },
        )
        cdp_frame, cdp_key = where["result"]["value"]
        key = await h.evaluate(element_fn("return EV.domKey(el);"))
        assert cdp_frame == [frame]
        assert cdp_key == key or cdp_key.startswith(key + "/"), (key, cdp_key)
        checked += 1
    assert checked >= 1
