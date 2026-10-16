"""Enter on a focused list in a form follows HTML implicit submission on every host.

Chromium submits the form on one platform and opens the list on another, so a capability
discovered on one host would stall on the other. The surface applies the HTML rule itself:
the default button's own activation, exactly once, never the native key as well. The policy
check sees that button, so Enter on a list can't post past layer 1."""

from typing import Any

import pytest

from eagleview.schema.action import Action
from eagleview.surfaces.web.playwright_surface import PlaywrightSurface
from tests.support.bank import Bank
from tests.surfaces.helpers import EPOCH, act_on, button, spec, to_member, wait_until

ENTER = Action(type="key", key="Return")


async def _enter_on(surface: PlaywrightSurface, selector: str) -> Any:
    await surface.page.focus(selector)
    result = await surface.act(ENTER, "automation", EPOCH)
    state = await surface.page.evaluate("({submits, keys, submitter})")
    return result, state


async def test_enter_presses_the_default_button_exactly_once(open_site: Any) -> None:
    surface = await open_site("implicit_submit.html")
    result, state = await _enter_on(surface, "#validate select")
    assert result.status == "done", result
    assert state == {"submits": {"validate": 1}, "keys": 0, "submitter": "Validate"}
    # The result still names the focused list, as the recorder expects of a key press.
    assert result.grounded is not None and result.grounded.tag == "select"


async def test_enter_on_a_list_whose_default_button_is_post_needs_approval(
    open_site: Any,
) -> None:
    surface = await open_site("implicit_submit.html")
    result, state = await _enter_on(surface, "#post select")
    assert result.status == "needs_approval"
    assert result.grounded is not None and result.grounded.name == "Post"
    assert state["submits"] == {} and state["keys"] == 0


@pytest.mark.parametrize(
    ("form", "submits"),
    [("disabled", {}), ("one_field", {"one_field": 1}), ("two_fields", {})],
)
async def test_without_an_enabled_default_button_the_html_rule_decides(
    open_site: Any, form: str, submits: dict[str, int]
) -> None:
    surface = await open_site("implicit_submit.html")
    result, state = await _enter_on(surface, f"#{form} select")
    assert result.status == "done", result
    assert state["submits"] == submits and state["keys"] == 0


async def test_a_list_outside_a_form_gets_the_key_as_it_is(open_site: Any) -> None:
    surface = await open_site("implicit_submit.html")
    result, state = await _enter_on(surface, "#loose")
    assert result.status == "done", result
    assert state["keys"] == 1 and state["submits"] == {}


async def test_enter_on_the_share_type_list_reaches_the_review(
    open_surface: Any, lantern: Bank
) -> None:
    """The step discovery recorded (choose a share type, press Return) on this host too."""
    surface = await open_surface(lantern)
    await to_member(surface, lantern, lantern.demo)
    assert (await act_on(surface, button("Open Share"))).ok
    await wait_until(surface, {"text_present": {"frame": "main", "text": "Open Share Account"}})
    share_type = spec(
        {
            "strategy": "anchor",
            "anchor_text": "Share Type",
            "relation": "right_of",
            "control": "select",
        },
        frame=["main"],
    )
    option = lantern.tenant.share_types["money_market"].description
    assert (await act_on(surface, share_type, "select", option=option)).ok
    assert (await surface.act(ENTER, "automation", EPOCH)).ok
    await wait_until(surface, {"text_present": {"frame": "main", "text": "Review - Open Share"}})
