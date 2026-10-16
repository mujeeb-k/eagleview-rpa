"""Screen-level redaction on the real Member Detail screen: everything the app profile marks as
personal is painted in the screenshot and scrubbed from the DOM snapshot, including text a
translucent modal covers but does not hide."""

import io
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from eagleview.artifacts.yaml_io import load_app_profile
from eagleview.evidence.writer import EvidenceWriter
from eagleview.safety.redaction import Redactor
from eagleview.schema.action import TextBox
from mock_bank.config import all_tenants
from mock_bank.seed import Member, generate
from tests.fixtures import APP_PROFILE
from tests.support.bank import Bank
from tests.support.replay import policy_for
from tests.support.teller import open_member

DEMO = "10482"


@cache
def demo_member() -> Member:
    tenant = next(t for t in all_tenants() if t.slug == "lantern-bay")
    return generate(tenant)[DEMO]


def personal(m: Member) -> list[str]:
    return [
        m.display_name,
        m.phone,
        m.email,
        m.street,
        f"{m.city}, {m.state} {m.zip}",
        *([m.notes] if m.notes else []),
    ]


def is_filled(png: bytes, b: TextBox) -> bool:
    """Filled solid, not just a dark glyph: every pixel of the box is black."""
    with Image.open(io.BytesIO(png)) as image:
        crop = image.convert("RGB").crop((b.x, b.y, b.x + b.width, b.y + b.height))
        return all(px == (0, 0, 0) for px in crop.get_flattened_data())


@pytest.mark.parametrize("modal", [False, True], ids=["plain", "under_unknown_modal"])
async def test_member_detail_personal_data_never_reaches_disk(
    open_surface: Any, lantern: Bank, tmp_path: Path, modal: bool
) -> None:
    if modal:
        lantern.set_faults(unknown_modal_on="/member")
    surface = await open_surface(lantern)
    await open_member(surface.page, lantern, DEMO)
    boxes = await surface.text_boxes(every=True)
    visible = {b.text for b in await surface.text_boxes()}
    if modal:
        # Covered by the overlay, so not "visible" for locating, yet readable in a screenshot.
        assert demo_member().street not in visible
        assert any(b.text == demo_member().street for b in boxes)

    writer = EvidenceWriter(tmp_path, "r_screen", Redactor.from_policy(policy_for(lantern)))
    writer.use_screen_labels(load_app_profile(APP_PROFILE).sensitive_labels)
    shot = writer.screenshot(1, "after", (await surface.observe()).screenshot, text_boxes=boxes)
    dom = writer.dom_snapshot("failure", await surface.dom_snapshot()).read_text()

    png = shot.read_bytes()
    by_text = {b.text: b for b in boxes}
    for value in personal(demo_member()):
        assert value in by_text, f"{value!r} is not on screen"
        assert is_filled(png, by_text[value]), value
        assert value not in dom, value
    # Labels and ordinary teller text stay readable.
    for kept in ["Member Detail", "Address:", "Share Accounts", "Regular Share"]:
        assert kept in dom
        if kept in by_text:
            assert not is_filled(png, by_text[kept]), kept
