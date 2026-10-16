"""Enter on a focused field in a form, by HTML implicit submission.

A list: Chromium submits the form on one platform and opens the list on another, so a
capability discovered on one host would stall on the other. The surface applies the HTML rule
itself: the default button's own activation, exactly once, never the native key as well.

A text field: the key stays the browser's own, so the page's keystroke handlers run.

Both: the policy check sees what Enter would submit (the default button, or with no button the
form's own action), so Enter can't post past layer 1. The result still names the focused field;
a refusal names the button in its detail."""

from typing import Any

import pytest

from eagleview.safety.policy_engine import TargetInfo
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
    assert result.decision is not None and "'Post'" in result.decision.detail
    assert result.grounded is not None and result.grounded.tag == "select"
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


# --- text fields ------------------------------------------------------------------------------


async def test_enter_in_a_text_field_whose_default_button_is_post_needs_approval(
    open_site: Any,
) -> None:
    surface = await open_site("implicit_submit.html")
    result, state = await _enter_on(surface, "#text_post input[name='amount']")
    assert result.status == "needs_approval"
    assert result.decision is not None and "'Post'" in result.decision.detail
    # The result names the focused field, as the recorder and compiler read it.
    assert result.grounded is not None and result.grounded.tag == "input"
    assert state["submits"] == {} and state["keys"] == 0


async def test_enter_in_a_text_field_presses_go_exactly_once(
    open_site: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    surface = await open_site("implicit_submit.html")
    seen: list[TargetInfo | None] = []
    check = surface.engine.check

    def spy(action: Action, actor: str, ctx: Any, target: TargetInfo | None = None) -> Any:
        seen.append(target)
        return check(action, actor, ctx, target)

    monkeypatch.setattr(surface.engine, "check", spy)
    result, state = await _enter_on(surface, "#text_go input")
    assert result.status == "done", result
    # Layer 1 saw the Go button, not the search field.
    assert [t.label if t else None for t in seen] == ["Go"]
    # The native key ran (the field's handler saw it) and the browser pressed Go, once.
    assert state == {"submits": {"text_go": 1}, "keys": 1, "submitter": "Go"}
    assert result.grounded is not None and result.grounded.input_type == "search"


async def test_enter_in_a_textarea_is_a_newline_not_a_submission(open_site: Any) -> None:
    surface = await open_site("implicit_submit.html")
    result, state = await _enter_on(surface, "#text_post textarea")
    assert result.status == "done", result
    assert state["submits"] == {} and state["keys"] == 1
    assert await surface.page.input_value("#text_post textarea") == "\n"
    # The text field beside it, in the same form, is a press on Post.
    sibling, _ = await _enter_on(surface, "#text_post input[name='amount']")
    assert sibling.status == "needs_approval"


async def test_a_disabled_default_button_is_not_a_press(open_site: Any) -> None:
    surface = await open_site("implicit_submit.html")
    result, state = await _enter_on(surface, "#text_disabled input[type='text']")
    assert result.status == "done", result
    assert state["submits"] == {} and state["keys"] == 1
    # The same Post, enabled, is.
    await surface.page.evaluate(
        "document.querySelector('#text_disabled [type=submit]').disabled = false"
    )
    enabled, state = await _enter_on(surface, "#text_disabled input[type='text']")
    assert enabled.status == "needs_approval"
    assert state["submits"] == {}


async def test_without_a_submit_button_layer_1_judges_where_the_form_goes(
    open_site: Any,
) -> None:
    """One text field and no button: the form submits itself, so layer 1 checks its method and
    action against the routes. Two text fields: nothing is submitted."""
    surface = await open_site("implicit_submit.html")
    alone, state = await _enter_on(surface, "#text_alone input")
    assert alone.status == "needs_approval", alone
    assert alone.decision is not None and "/share/post" in alone.decision.detail
    assert state["submits"] == {} and state["keys"] == 0
    pair, state = await _enter_on(surface, "#text_pair input[name='a']")
    assert pair.status == "done", pair
    assert state["submits"] == {} and state["keys"] == 1
