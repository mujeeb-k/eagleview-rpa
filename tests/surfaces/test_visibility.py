"""(c) Visibility is hit-tested. The mock bank's fake modals are absolutely positioned divs over a
pointer-blocking overlay: the text underneath is still in the DOM, and Playwright still calls it
"visible". To Eagleview it is not there until it is topmost at its own centre, through frames."""

import io
from pathlib import Path
from typing import Any

from PIL import Image

from eagleview.artifacts.yaml_io import load_capability, load_policy
from eagleview.evidence.writer import EvidenceWriter
from eagleview.safety.redaction import Redactor
from eagleview.schema.action import Action
from eagleview.schema.capability import TableSpec
from eagleview.surfaces.extract import ExtractError
from eagleview.surfaces.web.playwright_surface import PlaywrightSurface
from tests.fixtures import CAPABILITY, POLICY
from tests.support.bank import Bank
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

SHARES_TABLE = {
    "anchor_text": "Share Accounts",
    "frame": ["main"],
    "columns": {"suffix": "Sfx", "balance": "Balance"},
}


def text_present(text: str) -> Any:
    return pred({"text_present": {"frame": "main", "text": text}})


async def test_text_under_a_fake_modal_is_not_present(open_surface: Any, lantern: Bank) -> None:
    lantern.set_faults(unknown_modal_on="/member")
    surface: PlaywrightSurface = await open_surface(lantern)
    await to_member_under_modal(surface, lantern)
    main = surface.page.frame(name="main")
    assert main is not None

    # Playwright: in the DOM and "visible". Eagleview: covered, so not present.
    assert await main.get_by_text("Share Accounts").is_visible()
    assert not await surface.check(text_present("Share Accounts"))
    assert not await surface.check(text_present("Member Detail"))
    assert await surface.check(text_present("Cash Drawer Alert"))
    texts = {b.text for b in await surface.text_boxes()}
    assert "Share Accounts" not in texts and "Cash Drawer Alert" in texts
    # The nav frame is not covered by a modal inside main.
    assert await surface.check(pred({"text_present": {"frame": "nav", "text": "Member Inquiry"}}))

    # Controls underneath resolve (they exist) but are occluded: not present, not actionable,
    # not readable.
    open_share = await resolved(surface, button("Open Share"))
    assert open_share.occluded
    assert not await surface.check(
        pred({"element_present": {"target": button("Open Share").model_dump(mode="json")}})
    )
    result = await surface.act(Action(type="click"), "automation", EPOCH, target=open_share)
    assert result.status == "target_mismatch"
    try:
        await surface.extract(_table())
        raise AssertionError("extracting a covered table must fail")
    except ExtractError as exc:
        assert exc.code == "occluded"

    # A person dismisses it: tick "Supervisor notified", then Continue. Then it is all back.
    covered = await surface.observe()
    acknowledge = spec(
        {
            "strategy": "anchor",
            "anchor_text": "Supervisor notified",
            "relation": "left_of",
            "control": "checkbox",
        },
        frame=["main"],
    )
    assert (await act_on(surface, acknowledge)).ok
    assert (await act_on(surface, button("Continue"))).ok
    await wait_until(surface, {"text_present": {"frame": "main", "text": "Share Accounts"}})
    assert not await surface.check(text_present("Cash Drawer Alert"))
    assert (await surface.observe()).fingerprint != covered.fingerprint
    assert not (await resolved(surface, button("Open Share"))).occluded


async def to_member_under_modal(surface: PlaywrightSurface, bank: Bank) -> None:
    await to_inquiry(surface)
    assert (await act_on(surface, MEMBER_FIELD, "fill", text=bank.demo)).ok
    assert (await act_on(surface, GO_IMAGE)).ok
    await wait_until(surface, {"text_present": {"frame": "main", "text": "Cash Drawer Alert"}})


async def test_system_notice_is_dismissed_with_its_ok_button(
    open_surface: Any, lantern: Bank
) -> None:
    """The text strategy reads button labels (input values), so `{text: OK}` finds the button."""
    lantern.set_faults(system_notice_on="/inq")
    surface: PlaywrightSurface = await open_surface(lantern)
    assert (await act_on(surface, NAV_INQUIRY)).ok
    await wait_until(surface, {"text_present": {"frame": "main", "text": "System Notice"}})
    assert not await surface.check(text_present("Member Search"))
    ok = spec({"strategy": "text", "text": "OK"}, frame=["main"])
    assert (await act_on(surface, ok)).ok
    await wait_until(surface, {"text_present": {"frame": "main", "text": "Member Search"}})


async def test_typed_values_are_text_boxes_and_get_painted(
    open_surface: Any, lantern: Bank, tmp_path: Path
) -> None:
    """A typed member number is on screen but is not a DOM text node. It must still be a text box,
    or the after-screenshot of `enter_member_id` would be written with the number readable."""
    surface: PlaywrightSurface = await open_surface(lantern)
    await to_inquiry(surface)
    assert (await act_on(surface, MEMBER_FIELD, "fill", text=lantern.demo)).ok
    boxes = await surface.text_boxes()
    typed = [b for b in boxes if b.text == lantern.demo]
    assert len(typed) == 1 and typed[0].source == "control" and typed[0].frame == "main"
    # Button labels are control text too.
    assert any(b.text == "Sign Off" for b in boxes)

    redactor = Redactor.from_policy(load_policy(POLICY))
    redactor.use_capability(load_capability(CAPABILITY))
    redactor.register_inputs({"member_id": lantern.demo})
    writer = EvidenceWriter(tmp_path / "runs", "r_paint", redactor)
    shot = (await surface.observe()).screenshot
    path = writer.screenshot(1, "after", shot, text_boxes=boxes)
    box = typed[0]
    with Image.open(path) as img:
        rgb = img.convert("RGB")
        centre = rgb.getpixel((box.x + box.width // 2, box.y + box.height // 2))
    with Image.open(io.BytesIO(shot)) as raw:
        before = raw.convert("RGB").getpixel((box.x + box.width // 2, box.y + box.height // 2))
    assert centre == (0, 0, 0) and before != (0, 0, 0)


async def test_passwords_are_masked_in_text_boxes(open_surface: Any, lantern: Bank) -> None:
    surface: PlaywrightSurface = await open_surface(lantern, signed_on=False)
    await surface.page.goto(lantern.url + "/signon")
    await surface.page.fill("input[name='f2']", lantern.password)
    boxes = await surface.text_boxes()
    assert all(lantern.password not in b.text for b in boxes)
    assert any(set(b.text) == {"•"} and len(b.text) == len(lantern.password) for b in boxes)


async def test_off_screen_text_is_not_visible_until_scrolled(
    open_surface: Any, lantern: Bank
) -> None:
    surface: PlaywrightSurface = await open_surface(lantern)
    await to_member(surface, lantern, "10014")
    main = surface.page.frame(name="main")
    assert main is not None
    footer = "TRAINING REGION"
    assert await main.get_by_text(footer, exact=False).count() == 1
    below = await main.evaluate(
        "t => [...document.querySelectorAll('font')].find(f => f.innerText.includes(t))"
        ".getBoundingClientRect().top > window.innerHeight",
        footer,
    )
    assert below, "precondition: the footer starts below the fold"
    assert not await surface.check(pred({"text_matches": {"frame": "main", "pattern": footer}}))
    await main.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
    assert await surface.check(pred({"text_matches": {"frame": "main", "pattern": footer}}))


def _table() -> Any:
    return TableSpec.model_validate(SHARES_TABLE)
