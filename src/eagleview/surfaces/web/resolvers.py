"""Locator resolution on a web page. Each strategy finds every matching element. Exactly one is
unique; two or more is ambiguous, and replay never picks the first. Ranked resolution takes the
first locator that is unique, then checks agreement: the next locator that is also unique must
find the same element, and the result says whether it did."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from playwright.async_api import ElementHandle, Frame, JSHandle, Page
from playwright.async_api import Error as PlaywrightError

from eagleview.schema.action import TextBox
from eagleview.schema.common import SurfaceKind
from eagleview.schema.locators import (
    A11yLocator,
    AnchorLocator,
    CssLocator,
    Locator,
    PointLocator,
    TargetSpec,
    TextLocator,
    XPathLocator,
    strategy_applies,
)
from eagleview.surfaces.base import (
    Ambiguous,
    Attempt,
    Element,
    EventSink,
    NotFound,
    Resolution,
    Unique,
)
from eagleview.surfaces.geometry import Box, expand_aliases, resolve_anchor
from eagleview.surfaces.web.js import element_fn, page_fn


@dataclass(frozen=True)
class Found:
    status: Literal["unique", "ambiguous", "not_found", "skipped"]
    elements: tuple[Element, ...] = ()
    detail: str = ""

    @property
    def count(self) -> int:
        return len(self.elements)


def frame_path(frame: Frame) -> tuple[str, ...]:
    """Names from the top: ("main",). Unnamed frames are "#<index>" among their siblings."""
    parts: list[str] = []
    f = frame
    while f.parent_frame is not None:
        parent = f.parent_frame
        parts.append(f.name or f"#{parent.child_frames.index(f)}")
        f = parent
    return tuple(reversed(parts))


def frames_in_scope(page: Page, scope: Sequence[str]) -> list[tuple[tuple[str, ...], Frame]]:
    """Every frame when the scope is empty; otherwise exactly the frame at that path."""
    out: list[tuple[tuple[str, ...], Frame]] = []
    for frame in page.frames:
        path = frame_path(frame)
        if not scope or path == tuple(scope):
            out.append((path, frame))
    return out


async def split_pair(handle: JSHandle) -> tuple[Any, list[ElementHandle]]:
    """`{boxes, els}` from an evaluate_handle: the boxes as JSON, the elements as handles."""
    try:
        boxes = await (await handle.get_property("boxes")).json_value()
        els_handle = await handle.get_property("els")
        props = await els_handle.get_properties()
        els: list[ElementHandle] = []
        for i in range(len(props)):
            el = props[str(i)].as_element()
            if el is None:
                raise RuntimeError("a returned element was not an element")
            els.append(el)
        return boxes, els
    finally:
        await handle.dispose()


_TRANSIENT = (
    "execution context was destroyed",
    "frame was detached",
    "cannot find context",
    "target closed",
    "target page, context or browser has been closed",
    "element is not attached",
    "jshandle is disposed",
)


def is_transient(exc: PlaywrightError) -> bool:
    """The frame is mid-navigation or gone. What it shows right now is nothing: callers treat
    the frame as empty and the settle loop looks again, instead of failing the run."""
    message = str(exc).lower()
    return any(t in message for t in _TRANSIENT)


async def text_boxes_in(
    frame: Frame, path: tuple[str, ...]
) -> tuple[list[TextBox], list[ElementHandle], list[bool]]:
    """Visible text in one frame: boxes, the element carrying each, and heading flags. A frame
    that is navigating shows nothing."""
    try:
        handle = await frame.evaluate_handle(page_fn("return EV.textBoxes(doc);"))
        raw, els = await split_pair(handle)
    except PlaywrightError as exc:
        if is_transient(exc):
            return [], [], []
        raise
    boxes = [
        TextBox(
            text=b["text"],
            x=b["x"],
            y=b["y"],
            width=b["width"],
            height=b["height"],
            frame="/".join(path) or None,
            source=b["source"],
        )
        for b in raw
    ]
    return boxes, els, [bool(b["heading"]) for b in raw]


class Resolver:
    def __init__(
        self,
        page: Page,
        kind: SurfaceKind,
        *,
        label_aliases: Mapping[str, Sequence[str]] | None = None,
        app_fingerprint: str | None = None,
        sink: EventSink | None = None,
    ) -> None:
        self.page = page
        self.kind: SurfaceKind = kind
        self.aliases: Mapping[str, Sequence[str]] = label_aliases or {}
        self.app_fingerprint = app_fingerprint
        self.sink = sink

    # --- identity and geometry ----------------------------------------------------------------

    async def same(self, a: Element, b: Element) -> bool:
        if a.frame != b.frame:
            return False
        try:
            return bool(
                await a.handle.evaluate(element_fn("return EV.sameTarget(el, arg);"), b.handle)
            )
        except PlaywrightError:
            return False  # detached, or the frame navigated away

    async def point_for(self, el: Element, *, scroll: bool) -> dict[str, Any]:
        """{connected, rendered, x, y, topmost}; scrolls el into view first when asked."""
        try:
            return await el.handle.evaluate(element_fn("return EV.pointFor(el, arg);"), scroll)
        except PlaywrightError:
            return {"connected": False}

    async def handle_at(self, x: int, y: int) -> Element | None:
        """The element under a top-level point, descending through frames, climbed to what a
        click there means (the <a>, not the <font> inside it)."""
        frame = self.page.main_frame
        lx, ly = float(x), float(y)
        for _ in range(8):  # frames nest a few levels at most
            hit = await frame.evaluate_handle(
                page_fn("return doc.elementFromPoint(arg[0], arg[1]);"), [lx, ly]
            )
            el = hit.as_element()
            if el is None:
                await hit.dispose()
                return None
            info = await el.evaluate(
                element_fn(
                    "const r = el.getBoundingClientRect();"
                    "return [el.tagName, r.left + el.clientLeft, r.top + el.clientTop];"
                )
            )
            if info[0] in ("FRAME", "IFRAME"):
                child = await el.content_frame()
                await el.dispose()
                if child is None:
                    return None
                lx, ly = lx - info[1], ly - info[2]
                frame = child
                continue
            climbed = await frame.evaluate_handle(element_fn("return EV.climb(el);"), el)
            await el.dispose()
            target = climbed.as_element()
            if target is None:
                return None
            tag = await target.evaluate("e => e.tagName.toLowerCase()")
            return Element(target, frame_path(frame), tag)
        return None

    # --- strategies ---------------------------------------------------------------------------

    async def _collect(
        self, path: tuple[str, ...], frame: Frame, handles: list[ElementHandle]
    ) -> list[Element]:
        out: list[Element] = []
        for h in handles:
            tag = await h.evaluate("e => e.tagName.toLowerCase()")
            el = Element(h, path, tag)
            if not any([await self.same(el, seen) for seen in out]):
                out.append(el)
        return out

    async def find(self, loc: Locator, scope: Sequence[str]) -> Found:
        if not strategy_applies(loc.strategy, self.kind):
            return Found("skipped", detail=f"{loc.strategy} does not run on {self.kind}")
        if isinstance(loc, PointLocator):
            if self.app_fingerprint is None or loc.bound_to_fingerprint != self.app_fingerprint:
                return Found("skipped", detail="point is bound to a different app fingerprint")
            el = await self.handle_at(loc.x, loc.y)
            return _found([el] if el is not None else [])
        if isinstance(loc, AnchorLocator):
            return await self._anchor(loc, scope)
        elements: list[Element] = []
        for path, frame in frames_in_scope(self.page, scope):
            handles: list[ElementHandle] = []
            try:
                for locator in self._playwright_locators(loc, frame):
                    handles += await locator.filter(visible=True).element_handles()
                elements += await self._collect(path, frame, handles)
            except PlaywrightError as exc:
                if is_transient(exc):
                    continue
                if "selector" in str(exc).lower() or "xpath" in str(exc).lower():
                    return Found("not_found", detail=f"invalid {loc.strategy}: {exc}")
                raise
        return _found(elements)

    def _playwright_locators(self, loc: Locator, frame: Frame) -> list[Any]:
        match loc:
            case A11yLocator(role=role, name=name):
                return [
                    frame.get_by_role(role, name=n, exact=True)  # pyright: ignore[reportArgumentType]
                    for n in expand_aliases([name], self.aliases)
                ]
            case TextLocator(text=text, exact=exact):
                texts = expand_aliases([text], self.aliases)
                return [frame.get_by_text(t, exact=exact) for t in texts]
            case CssLocator(selector=selector):
                return [frame.locator(f"css={selector}")]
            case XPathLocator(expression=expression):
                return [frame.locator(f"xpath={expression}")]
            case _:
                return []

    async def _anchor(self, loc: AnchorLocator, scope: Sequence[str]) -> Found:
        """Geometry over text boxes, frame by frame: a label and its control share a frame."""
        winners: list[Element] = []
        for path, frame in frames_in_scope(self.page, scope):
            texts, text_els, _ = await text_boxes_in(frame, path)
            try:
                if loc.control == "text":
                    controls = [Box.of(i, t) for i, t in enumerate(texts) if t.source == "text"]
                    control_els = text_els
                else:
                    handle = await frame.evaluate_handle(
                        page_fn("return EV.controls(doc, arg);"), loc.control
                    )
                    raw, control_els = await split_pair(handle)
                    controls = [
                        Box(i, b["x"], b["y"], b["width"], b["height"]) for i, b in enumerate(raw)
                    ]
                result = resolve_anchor(loc, texts, controls, self.aliases)
                if result.status == "ambiguous" and not result.keys:
                    return Found("ambiguous", detail=result.detail)
                picked = [control_els[k] for k in result.keys]
                winners += await self._collect(path, frame, picked)
            except PlaywrightError as exc:
                if not is_transient(exc):
                    raise
        return _found(winners)

    # --- ranked resolution --------------------------------------------------------------------

    async def resolve(self, spec: TargetSpec) -> Resolution:
        attempts: list[Attempt] = []
        winner: tuple[int, Locator, Element] | None = None
        agreement: bool | None = None
        agreement_strategy = None
        ambiguous_count = 0
        for rank, loc in enumerate(spec.locators):
            found = await self.find(loc, spec.frame)
            attempts.append(Attempt(rank, loc.strategy, found.status, found.count, found.detail))
            if found.status == "ambiguous":
                ambiguous_count = ambiguous_count or max(found.count, 2)
            if found.status != "unique":
                continue
            if winner is None:
                winner = (rank, loc, found.elements[0])
                continue
            agreement = await self.same(winner[2], found.elements[0])
            agreement_strategy = loc.strategy
            break
        result: Resolution
        if winner is not None:
            rank, loc, el = winner
            point = await self.point_for(el, scroll=True)
            occluded = not (point.get("rendered") and point.get("topmost"))
            result = Unique(
                el, loc.strategy, rank, agreement, agreement_strategy, occluded, tuple(attempts)
            )
        elif ambiguous_count:
            result = Ambiguous(ambiguous_count, tuple(attempts))
        else:
            result = NotFound(tuple(attempts))
        self._log(spec, result)
        return result

    def _log(self, spec: TargetSpec, result: Resolution) -> None:
        if self.sink is None:
            return
        fields: dict[str, object] = {
            "target": spec.description,
            "attempts": [
                {"rank": a.rank, "strategy": a.strategy, "outcome": a.outcome, "count": a.count}
                for a in result.attempts
            ],
        }
        match result:
            case Unique():
                fields |= {
                    "result": "unique",
                    "strategy": result.strategy,
                    "rank": result.rank,
                    "agreement": result.agreement,
                    "agreement_strategy": result.agreement_strategy,
                    "occluded": result.occluded,
                }
            case Ambiguous():
                fields |= {"result": "ambiguous", "count": result.count}
            case NotFound():
                fields |= {"result": "not_found"}
        self.sink.event("locator.resolve", "system", **fields)


def _found(elements: list[Element]) -> Found:
    if not elements:
        return Found("not_found")
    if len(elements) > 1:
        return Found("ambiguous", tuple(elements), f"{len(elements)} elements match")
    return Found("unique", tuple(elements))
