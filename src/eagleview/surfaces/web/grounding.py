"""Grounding: from a pixel (or the keyboard focus) to the element there, described so a person
and every locator strategy can find it again.

Two hit tests run at the same point. CDP `DOM.getNodeForLocation` gives the node the browser's
own hit test reports, so `Accessibility.getPartialAXTree` can name it (role and accessible name
need the browser's accessibility tree). A JS `elementFromPoint` descent through the frames gives a
Playwright handle to the same node, so resolvers can be compared against it. Both are climbed to
the element a click there means, and their DOM keys must agree, or the page changed in between
and grounding is retried."""

import uuid
from dataclasses import dataclass
from typing import Any

from playwright.async_api import CDPSession, Page

from eagleview.schema.locators import (
    A11yLocator,
    AnchorLocator,
    Control,
    CssLocator,
    Locator,
    PointLocator,
    Relation,
    TextLocator,
    XPathLocator,
)
from eagleview.schema.surface import BBox, GroundedTarget
from eagleview.surfaces.base import Element
from eagleview.surfaces.geometry import Box, nearest_text
from eagleview.surfaces.web.js import cdp_fn, element_fn
from eagleview.surfaces.web.resolvers import Resolver, frames_in_scope, text_boxes_in

# Chrome's accessibility roles that a person (and Playwright's get_by_role) can name a control by.
# Chrome says "image" where ARIA and Playwright say "img".
_A11Y_ROLES = {
    "link": "link",
    "button": "button",
    "textbox": "textbox",
    "searchbox": "searchbox",
    "image": "img",
    "img": "img",
    "combobox": "combobox",
    "listbox": "listbox",
    "radio": "radio",
    "checkbox": "checkbox",
    "heading": "heading",
    "menuitem": "menuitem",
    "tab": "tab",
    "option": "option",
    "spinbutton": "spinbutton",
    "switch": "switch",
}
_CONTROLS: frozenset[str] = frozenset(
    {"textbox", "button", "link", "select", "checkbox", "radio", "image", "text"}
)


class GroundingError(Exception):
    """Nothing groundable at the point, or the page kept changing under the two hit tests."""


@dataclass(frozen=True)
class Grounding:
    target: GroundedTarget
    element: Element  # a live handle to the grounded element
    backend_id: int  # the browser's node id, for exact focus checks
    frame_url: str
    control: str  # the anchor control kind this element is ("textbox", "link", "text", ...)
    css: tuple[str, ...]
    xpath: tuple[str, ...]


class Grounder:
    def __init__(self, page: Page, cdp: CDPSession, resolver: Resolver) -> None:
        self.page = page
        self.cdp = cdp
        self.resolver = resolver

    async def at_point(self, x: int, y: int) -> Grounding:
        for _ in range(2):
            grounding = await self._at_point_once(x, y)
            if grounding is not None:
                return grounding
        raise GroundingError(f"the page changed while grounding ({x}, {y})")

    async def focus(self) -> GroundedTarget | None:
        """The deep keyboard focus through frames, described like a pointer target. None when the
        focus is on a document body (nothing in particular)."""
        group = f"ev-{uuid.uuid4().hex}"
        try:
            res = await self.cdp.send(
                "Runtime.evaluate",
                {"expression": "(" + _DEEP_ACTIVE + ")()", "objectGroup": group},
            )
            obj = res.get("result", {})
            if obj.get("subtype") != "node" or "objectId" not in obj:
                return None
            info = await self._describe(obj["objectId"], group)
            if info is None or info["desc"]["tag"] in ("body", "frameset", "html"):
                return None
            return self._target(info, (0, 0), [])
        finally:
            await self.cdp.send("Runtime.releaseObjectGroup", {"objectGroup": group})

    async def focused_backend_id(self) -> int | None:
        group = f"ev-{uuid.uuid4().hex}"
        try:
            res = await self.cdp.send(
                "Runtime.evaluate",
                {"expression": "(" + _DEEP_ACTIVE + ")()", "objectGroup": group},
            )
            obj = res.get("result", {})
            if obj.get("subtype") != "node" or "objectId" not in obj:
                return None
            node = await self.cdp.send("DOM.describeNode", {"objectId": obj["objectId"]})
            return int(node["node"]["backendNodeId"])
        finally:
            await self.cdp.send("Runtime.releaseObjectGroup", {"objectGroup": group})

    async def _at_point_once(self, x: int, y: int) -> Grounding | None:
        group = f"ev-{uuid.uuid4().hex}"
        try:
            hit = await self.cdp.send(
                "DOM.getNodeForLocation",
                {"x": x, "y": y, "ignorePointerEventsNone": True},
            )
            resolved = await self.cdp.send(
                "DOM.resolveNode", {"backendNodeId": hit["backendNodeId"], "objectGroup": group}
            )
            climbed = await self.cdp.send(
                "Runtime.callFunctionOn",
                {
                    "objectId": resolved["object"]["objectId"],
                    "functionDeclaration": cdp_fn("return EV.climb(el);"),
                    "objectGroup": group,
                },
            )
            object_id = climbed.get("result", {}).get("objectId")
            if object_id is None:
                raise GroundingError(f"nothing to ground at ({x}, {y})")
            info = await self._describe(object_id, group)
        finally:
            await self.cdp.send("Runtime.releaseObjectGroup", {"objectGroup": group})
        if info is None:
            raise GroundingError(f"nothing to ground at ({x}, {y})")
        element = await self.resolver.handle_at(x, y)
        if element is None:
            return None
        js_key = await element.handle.evaluate(element_fn("return EV.domKey(el);"))
        desc = info["desc"]
        if js_key != desc["key"] or tuple(desc["frame"]) != element.frame:
            return None  # the DOM moved between the two hit tests
        frame_url = next(
            (
                f.url
                for p, f in frames_in_scope(self.page, list(element.frame))
                if p == element.frame
            ),
            self.page.url,
        )
        texts, _, _ = await self._frame_texts(element.frame)
        target = self._target(info, (x, y), texts)
        return Grounding(
            target=target,
            element=element,
            backend_id=info["backend_id"],
            frame_url=frame_url,
            control=desc["control"],
            css=tuple(desc["css"]),
            xpath=tuple(desc["xpath"]),
        )

    async def _describe(self, object_id: str, group: str) -> dict[str, Any] | None:
        node = await self.cdp.send("DOM.describeNode", {"objectId": object_id})
        backend_id = int(node["node"]["backendNodeId"])
        if node["node"].get("nodeType") != 1:
            return None
        desc = await self.cdp.send(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": cdp_fn("return EV.describe(el);"),
                "returnByValue": True,
                "objectGroup": group,
            },
        )
        ax = await self.cdp.send(
            "Accessibility.getPartialAXTree", {"backendNodeId": backend_id, "fetchRelatives": False}
        )
        role = name = None
        for n in ax.get("nodes", []):
            if n.get("backendDOMNodeId") == backend_id and not n.get("ignored"):
                raw_role = n.get("role", {}).get("value")
                role = _A11Y_ROLES.get(raw_role) if isinstance(raw_role, str) else None
                raw_name = n.get("name", {}).get("value")
                name = " ".join(raw_name.split()) if isinstance(raw_name, str) else None
        return {
            "backend_id": backend_id,
            "desc": desc["result"]["value"],
            "role": role,
            "name": name or None,
        }

    async def _frame_texts(self, path: tuple[str, ...]) -> tuple[list[Any], list[Any], list[bool]]:
        for p, frame in frames_in_scope(self.page, list(path)):
            if p == path:
                return await text_boxes_in(frame, path)
        return [], [], []

    @staticmethod
    def _target(info: dict[str, Any], point: tuple[int, int], texts: list[Any]) -> GroundedTarget:
        desc = info["desc"]
        b = desc["bbox"]
        bbox = BBox(x=b["x"], y=b["y"], width=max(0, b["width"]), height=max(0, b["height"]))
        box = Box(0, bbox.x, bbox.y, bbox.width, bbox.height)
        return GroundedTarget(
            point=point,
            frame=list(desc["frame"]),
            tag=desc["tag"],
            input_type=desc["input_type"],
            role=info["role"],
            name=info["name"],
            label=desc["label"],
            text=desc["text"],
            row_header=desc["row_header"],
            anchor_left=nearest_text(box, texts, "left_of") if texts else None,
            anchor_right=nearest_text(box, texts, "right_of") if texts else None,
            anchor_above=nearest_text(box, texts, "above") if texts else None,
            bbox=bbox,
        )

    # --- candidate locators -------------------------------------------------------------------

    async def with_candidates(
        self, grounding: Grounding, app_fingerprint: str | None
    ) -> GroundedTarget:
        """Propose locators in rank order and keep those that resolve uniquely to this element."""
        t = grounding.target
        proposals: list[Locator] = []
        if t.role is not None and t.name:
            proposals.append(A11yLocator(strategy="a11y", role=t.role, name=t.name))
        control = grounding.control if grounding.control in _CONTROLS else "any"
        anchors: list[tuple[str | None, Relation]] = [
            (t.anchor_left, "right_of"),
            (t.anchor_right, "left_of"),
            (t.anchor_above, "below"),
        ]
        for text, relation in anchors:
            if text:
                proposals.append(
                    AnchorLocator(
                        strategy="anchor",
                        anchor_text=[text],
                        relation=relation,
                        control=_as_control(control),
                    )
                )
        if t.text and grounding.control != "textbox":
            proposals.append(TextLocator(strategy="text", text=t.text, exact=True))
        css = [CssLocator(strategy="css", selector=s) for s in grounding.css]
        xpath = [XPathLocator(strategy="xpath", expression=e) for e in grounding.xpath]

        kept: list[Locator] = []
        for loc in proposals:
            if await self._verifies(loc, grounding):
                kept.append(loc)
        for group in (css, xpath):
            for loc in group:  # the first selector that verifies is enough
                if await self._verifies(loc, grounding):
                    kept.append(loc)
                    break
        if app_fingerprint is not None:
            x, y = t.point
            point = PointLocator(strategy="point", x=x, y=y, bound_to_fingerprint=app_fingerprint)
            if await self._verifies(point, grounding):
                kept.append(point)
        return t.model_copy(update={"candidates": kept})

    async def _verifies(self, loc: Locator, grounding: Grounding) -> bool:
        found = await self.resolver.find(loc, grounding.target.frame)
        if found.status != "unique":
            return False
        return await self.resolver.same(found.elements[0], grounding.element)


def _as_control(kind: str) -> Control:
    match kind:
        case "textbox" | "button" | "link" | "select" | "checkbox" | "radio" | "image" | "text":
            return kind
        case _:
            return "any"


_DEEP_ACTIVE = """function () {
  let el = document.activeElement;
  while (el && (el.tagName === 'FRAME' || el.tagName === 'IFRAME') && el.contentDocument) {
    el = el.contentDocument.activeElement;
  }
  return el;
}"""
