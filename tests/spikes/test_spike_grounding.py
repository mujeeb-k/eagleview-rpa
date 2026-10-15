"""Spike A: map a viewport pixel to a DOM node and its accessible role/name across nested frames.

Method under test: CDP ``DOM.getNodeForLocation`` (hit test in top-level viewport coordinates)
followed by ``Accessibility.getPartialAXTree`` on the returned backend node. The page is a real
two-level ``<frameset>`` and the inner ``main`` frame is scrolled, so a pass shows that the hit
test pierces frames and accounts for frame offsets and in-frame scroll without help from us.
"""

from dataclasses import dataclass
from typing import Any

from playwright.async_api import BrowserContext, CDPSession, Frame

# AX roles that carry no meaning for a reviewer; grounding walks past them to an ancestor.
_STRUCTURAL_ROLES = {"none", "generic", "StaticText", "InlineTextBox"}


@dataclass(frozen=True)
class Hit:
    frame_id: str
    node_name: str
    role: str
    name: str


def _frame_ids_by_name(tree: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    frame = tree["frame"]
    if frame.get("name"):
        out[frame["name"]] = frame["id"]
    for child in tree.get("childFrames", []):
        out.update(_frame_ids_by_name(child))
    return out


async def _ground(cdp: CDPSession, x: int, y: int) -> Hit:
    loc = await cdp.send(
        "DOM.getNodeForLocation", {"x": x, "y": y, "ignorePointerEventsNone": True}
    )
    backend_id = loc["backendNodeId"]
    described = await cdp.send("DOM.describeNode", {"backendNodeId": backend_id})
    ax = await cdp.send(
        "Accessibility.getPartialAXTree", {"backendNodeId": backend_id, "fetchRelatives": True}
    )
    nodes = {n["nodeId"]: n for n in ax["nodes"]}
    node = next(n for n in ax["nodes"] if n.get("backendDOMNodeId") == backend_id)
    # The hit point is often an inline wrapper (<font> inside <a>): climb to a meaningful node.
    while node.get("ignored") or node.get("role", {}).get("value") in _STRUCTURAL_ROLES:
        node = nodes[node["parentId"]]
    return Hit(
        frame_id=loc["frameId"],
        node_name=described["node"]["nodeName"],
        role=node["role"]["value"],
        name=node.get("name", {}).get("value", ""),
    )


async def _center(frame: Frame, selector: str) -> tuple[int, int]:
    box = await frame.locator(selector).bounding_box()
    assert box is not None
    return int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2)


async def test_cdp_hit_test_pierces_nested_frames(context: BrowserContext, pages_url: str) -> None:
    page = await context.new_page()
    await page.goto(f"{pages_url}/frameset.html")
    nav, main = page.frame(name="nav"), page.frame(name="main")
    assert nav is not None and main is not None
    await main.evaluate("window.scrollTo(0, 400)")
    await main.wait_for_function("window.scrollY === 400", timeout=2000)

    cdp = await context.new_cdp_session(page)
    await cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
    frame_ids = _frame_ids_by_name((await cdp.send("Page.getFrameTree"))["frameTree"])
    assert {"nav", "banner", "main"} <= frame_ids.keys()
    assert len(set(frame_ids.values())) == len(frame_ids)

    link = await _ground(cdp, *await _center(nav, "a"))
    assert link == Hit(frame_ids["nav"], "FONT", "link", "Member Inquiry")

    # The "Member #" label sits in a sibling <td>, so the textbox has no accessible name.
    textbox = await _ground(cdp, *await _center(main, "input[name='txtA']"))
    assert textbox == Hit(frame_ids["main"], "INPUT", "textbox", "")

    # The GO image has no alt text: role only, empty name. This is why the anchor strategy exists.
    go = await _ground(cdp, *await _center(main, "img"))
    assert go == Hit(frame_ids["main"], "IMG", "image", "")
