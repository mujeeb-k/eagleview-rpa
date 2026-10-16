"""PlaywrightSurface: the web implementation of the Surface seam, for legacy browser apps.

`act()` is the choke point. For every action it does, in this order:
1. check the lease holder and epoch;
2. work out the exact point (scrolling a resolved target into view first);
3. ground that point now, immediately before acting, never after, because the page may have
   changed since the target was resolved;
4. if the caller resolved a target, require the element under the point to be that element,
   or refuse with `target_mismatch` and do nothing;
5. ask the policy, giving it the grounded label and role, not the caller's idea of them;
6. act at that same point with the mouse or keyboard, never via a selector that could retarget.

Fill types its value one key at a time (keydown, keypress, input, keyup per character), because
legacy screens hang validation and formatting on per-keystroke handlers. Setting `.value`
directly would skip them."""

import asyncio
import contextlib
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any, Self
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, CDPSession, Page
from playwright.async_api import Error as PlaywrightError

from eagleview.safety.policy_engine import (
    ApprovalToken,
    Decision,
    PolicyContext,
    PolicyEngine,
    TargetInfo,
    normalize_key,
)
from eagleview.schema.action import OBSERVE_ACTIONS, Action, TextBox
from eagleview.schema.capability import ScalarExtract, TableSpec
from eagleview.schema.common import SurfaceKind
from eagleview.schema.locators import TargetSpec
from eagleview.schema.predicates import (
    AnyPredicate,
    ElementPresent,
    FieldValue,
    OutputPresent,
    TextMatches,
    TextPresent,
    TitleMatches,
    UrlMatches,
)
from eagleview.schema.surface import GroundedTarget
from eagleview.surfaces.base import (
    ActionResult,
    ActStatus,
    BlockedRequest,
    EventSink,
    HolderGuard,
    NotHolder,
    Observation,
    PredicateContext,
    Resolution,
    Unique,
)
from eagleview.surfaces.extract import ExtractError, Grid, RawValue, map_columns
from eagleview.surfaces.geometry import expand_aliases, group_lines, label_key, normalize_text
from eagleview.surfaces.predicates import evaluate
from eagleview.surfaces.web.grounding import Grounder, Grounding, GroundingError
from eagleview.surfaces.web.js import element_fn, page_fn
from eagleview.surfaces.web.network_guard import GuardState, NetworkGuard
from eagleview.surfaces.web.resolvers import (
    Resolver,
    frame_path,
    frames_in_scope,
    is_transient,
    text_boxes_in,
)

# Pinned geometry: screenshot pixels, CSS pixels and CDP coordinates all agree at scale 1.
VIEWPORT = {"width": 1280, "height": 800}

_POINTER = frozenset({"click", "double_click", "move", "scroll", "fill", "select"})
_KEYBOARD = frozenset({"type", "key"})
# Printable ASCII. Playwright types anything else with insertText, which fires no key events, so
# a legacy keystroke handler would never see it. Refused rather than silently half-typed.
_TYPEABLE = re.compile(r"^[\x20-\x7e]*$")
_MAX_WAIT_MS = 10_000

_KEY_NAMES = {
    "return": "Enter",
    "enter": "Enter",
    "kpenter": "Enter",
    "tab": "Tab",
    "escape": "Escape",
    "esc": "Escape",
    "backspace": "Backspace",
    "delete": "Delete",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "left": "ArrowLeft",
    "right": "ArrowRight",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "space": "Space",
}
_MODIFIER_NAMES = {"ctrl": "Control", "alt": "Alt", "shift": "Shift", "meta": "Meta"}


def playwright_key(key: str) -> str:
    """xdotool-style (`Return`, `shift+Tab`, `ctrl+a`) to Playwright (`Enter`, `Shift+Tab`)."""
    *mods, base = normalize_key(key).split("+")
    name = _KEY_NAMES.get(base, base if len(base) == 1 else base.capitalize())
    return "+".join([*(_MODIFIER_NAMES[m] for m in mods), name])


_DIGITS = re.compile(r"\d")


def _shape(text: str) -> str:
    """Digits normalized, so a countdown, a member number or a date doesn't change the state."""
    return _DIGITS.sub("#", normalize_text(text))


def _url_shape(url: str) -> str:
    parts = urlsplit(url)
    return _shape(parts.path + (f"?{parts.query}" if parts.query else ""))


class PlaywrightSurface:
    kind: SurfaceKind = "legacy_web"

    def __init__(
        self,
        context: BrowserContext,
        page: Page,
        cdp: CDPSession,
        engine: PolicyEngine,
        guard_state: GuardState,
        guard: NetworkGuard,
        holder: HolderGuard,
        *,
        sink: EventSink | None = None,
        label_aliases: Mapping[str, Sequence[str]] | None = None,
        app_fingerprint: str | None = None,
    ) -> None:
        self.context = context
        self.page = page
        self.cdp = cdp
        self.engine = engine
        self.holder = holder
        self.guard = guard
        self._guard_state = guard_state
        self.sink = sink
        self.aliases: Mapping[str, Sequence[str]] = label_aliases or {}
        self.resolver = Resolver(
            page, self.kind, label_aliases=self.aliases, app_fingerprint=app_fingerprint, sink=sink
        )
        self.grounder = Grounder(page, cdp, self.resolver)
        # Discovery sets this so each act() records verified candidate locators for the recorder.
        self.record_candidates = False
        self._navigations = 0
        page.on("framenavigated", self._count_navigation)

    def _count_navigation(self, _frame: object) -> None:
        self._navigations += 1

    @classmethod
    async def open(
        cls,
        browser: Browser,
        engine: PolicyEngine,
        ctx: PolicyContext,
        holder: HolderGuard,
        *,
        sink: EventSink | None = None,
        label_aliases: Mapping[str, Sequence[str]] | None = None,
        app_fingerprint: str | None = None,
        actor: str = "harness",
    ) -> Self:
        """A fresh context at the pinned geometry, with the network guard installed before the
        first page exists, so no request ever escapes it. Traces, video and HAR stay off."""
        context = await browser.new_context(viewport=VIEWPORT, device_scale_factor=1)  # type: ignore[arg-type]
        state = GuardState(actor, ctx)
        guard = NetworkGuard(engine, lambda: state, sink)
        await guard.install(context)
        page = await context.new_page()
        cdp = await context.new_cdp_session(page)
        return cls(
            context,
            page,
            cdp,
            engine,
            state,
            guard,
            holder,
            sink=sink,
            label_aliases=label_aliases,
            app_fingerprint=app_fingerprint,
        )

    async def close(self) -> None:
        await self.context.close()

    # --- policy context -----------------------------------------------------------------------

    @property
    def policy_ctx(self) -> PolicyContext:
        return self._guard_state.ctx

    @policy_ctx.setter
    def policy_ctx(self, ctx: PolicyContext) -> None:
        self._guard_state.ctx = ctx

    @property
    def app_fingerprint(self) -> str | None:
        return self.resolver.app_fingerprint

    @app_fingerprint.setter
    def app_fingerprint(self, value: str | None) -> None:
        self.resolver.app_fingerprint = value

    def grant_approval(self, token: ApprovalToken) -> None:
        ctx = self.policy_ctx
        self.policy_ctx = replace(ctx, approvals=ctx.approvals | {token})

    def drain_blocked(self) -> list[BlockedRequest]:
        return self.guard.drain()

    def navigation_count(self) -> int:
        return self._navigations

    # --- screencast (the operator console's live view; observation only) ----------------------

    async def start_screencast(self, on_frame: Callable[[str], Awaitable[None]]) -> None:
        """Stream JPEG frames (base64) to `on_frame`, acknowledging each, as Spike B showed.
        Frames go to the operator's browser only and never touch disk. Chromium sends a frame
        only when the page repaints."""

        async def frame(params: dict[str, Any]) -> None:
            await self.cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
            await on_frame(params["data"])

        self._screencast_handler = lambda p: asyncio.ensure_future(frame(p))
        self.cdp.on("Page.screencastFrame", self._screencast_handler)
        await self.cdp.send(
            "Page.startScreencast",
            {"format": "jpeg", "quality": 70, "maxWidth": 1280, "maxHeight": 800},
        )

    async def stop_screencast(self) -> None:
        handler = getattr(self, "_screencast_handler", None)
        if handler is None:
            return
        self.cdp.remove_listener("Page.screencastFrame", handler)
        self._screencast_handler = None
        with contextlib.suppress(PlaywrightError):
            await self.cdp.send("Page.stopScreencast")

    def _emit(self, kind: str, actor: str, **fields: object) -> None:
        if self.sink is not None:
            self.sink.event(kind, actor, **fields)

    # --- observe ------------------------------------------------------------------------------

    async def observe(self) -> Observation:
        png = await self.page.screenshot(type="png", animations="disabled", caret="hide")
        headings: list[str] = []
        frames: list[tuple[str, str]] = []
        for frame in self.page.frames:
            path = frame_path(frame)
            frames.append(("/".join(path), _url_shape(frame.url)))
            boxes, _, flags = await text_boxes_in(frame, path)
            headings += [_shape(b.text) for b, heading in zip(boxes, flags, strict=True) if heading]
        title = await self.page.title()
        state = {"title": _shape(title), "frames": frames, "headings": headings}
        digest = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
        return Observation(
            screenshot=png,
            width=VIEWPORT["width"],
            height=VIEWPORT["height"],
            fingerprint=f"sha256:{digest}",
            url=self.page.url,
            title=title,
            frames=tuple(frames),
            headings=tuple(headings),
        )

    async def text_boxes(self, *, every: bool = False) -> list[TextBox]:
        out: list[TextBox] = []
        for frame in self.page.frames:
            boxes, _, _ = await text_boxes_in(frame, frame_path(frame), every=every)
            out += boxes
        return out

    async def dom_snapshot(self) -> str:
        parts: list[str] = []
        for frame in self.page.frames:
            path = "/".join(frame_path(frame)) or "top"
            try:
                markup = await frame.content()
            except PlaywrightError as exc:
                if not is_transient(exc):
                    raise
                markup = "(navigating)"
            parts.append(f"<!-- frame: {path} -->\n{markup}")
        return "\n".join(parts)

    # --- ground and resolve -------------------------------------------------------------------

    async def ground(self, x: int, y: int) -> GroundedTarget:
        grounding = await self.grounder.at_point(x, y)
        return await self.grounder.with_candidates(grounding, self.app_fingerprint)

    async def resolve(self, target: TargetSpec) -> Resolution:
        return await self.resolver.resolve(target)

    # --- act ----------------------------------------------------------------------------------

    async def act(
        self,
        action: Action,
        actor: str,
        epoch: int,
        *,
        target: Unique | None = None,
        step_id: str | None = None,
    ) -> ActionResult:
        result = await self._act(action, actor, epoch, target, step_id)
        g = result.grounded
        self._emit(
            "surface.act",
            actor,
            action=action.type,
            status=result.status,
            reason=result.reason,
            point=list(result.point) if result.point else None,
            step_id=step_id,
            target=None if g is None else _loggable(g),
            # The harness types credentials; even a password's length stays off disk.
            text_length=(
                len(action.text) if action.text is not None and actor != "harness" else None
            ),
        )
        return result

    async def _act(
        self,
        action: Action,
        actor: str,
        epoch: int,
        target: Unique | None,
        step_id: str | None,
    ) -> ActionResult:
        try:
            self.holder.assert_holder(actor, epoch)
        except NotHolder as exc:
            return ActionResult("not_holder", action, actor, str(exc))
        ctx = replace(self.policy_ctx, step_id=step_id)

        if action.type in OBSERVE_ACTIONS:
            decision = self.engine.check(action, actor, ctx)
            if not decision.allowed:
                return _refused(action, actor, decision)
            return await self._observe_action(action, actor, decision)

        if action.type == "navigate":
            decision = self.engine.check(action, actor, replace(ctx, current_url=self.page.url))
            if not decision.allowed:
                return _refused(action, actor, decision)
            self._bind(actor, ctx)
            assert action.url is not None
            try:
                await self.page.goto(action.url, wait_until="commit")
            except PlaywrightError as exc:
                return ActionResult(
                    "error", action, actor, f"navigation failed: {exc}", decision=decision
                )
            return ActionResult("done", action, actor, decision=decision)

        if action.type in _KEYBOARD:
            return await self._keyboard(action, actor, ctx)
        if action.type in _POINTER:
            return await self._pointer(action, actor, ctx, target)
        decision = self.engine.check(action, actor, ctx)
        return (
            _refused(action, actor, decision)
            if not decision.allowed
            else ActionResult(
                "error", action, actor, f"{action.type} is not supported on this surface"
            )
        )

    def _bind(self, actor: str, ctx: PolicyContext) -> None:
        """From here on, requests are attributed to this actor and step."""
        self._guard_state.actor = actor
        self._guard_state.ctx = ctx

    async def _observe_action(self, action: Action, actor: str, decision: Decision) -> ActionResult:
        match action.type:
            case "screenshot":
                image = (await self.observe()).screenshot
                return ActionResult("done", action, actor, decision=decision, image=image)
            case "zoom":
                if action.region is None:
                    return ActionResult("error", action, actor, "zoom needs a region")
                x0, y0, x1, y1 = action.region
                x0, y0 = max(0, x0), max(0, y0)
                x1, y1 = min(VIEWPORT["width"], x1), min(VIEWPORT["height"], y1)
                if x1 <= x0 or y1 <= y0:
                    return ActionResult("error", action, actor, "zoom region is empty")
                clip = {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}
                image = await self.page.screenshot(type="png", clip=clip)  # type: ignore[arg-type]
                return ActionResult("done", action, actor, decision=decision, image=image)
            case _:  # wait: the discovery model's pause. Replay polls predicates, never sleeps.
                if actor != "llm":
                    return ActionResult("error", action, actor, "wait is for the discovery model")
                ms = min(action.duration_ms or 1000, _MAX_WAIT_MS)
                await asyncio.sleep(ms / 1000)
                return ActionResult("done", action, actor, decision=decision)

    async def _pointer(
        self, action: Action, actor: str, ctx: PolicyContext, target: Unique | None
    ) -> ActionResult:
        if action.type == "fill" and not _TYPEABLE.fullmatch(action.text or ""):
            return ActionResult("error", action, actor, "fill text must be printable ASCII")
        if target is not None:
            if target.occluded:
                return ActionResult("target_mismatch", action, actor, "the target is covered")
            prep = await self.resolver.point_for(target.element, scroll=True)
            if not prep.get("connected") or not prep.get("rendered"):
                return ActionResult("target_mismatch", action, actor, "the target is gone")
            point = (int(prep["x"]), int(prep["y"]))
        elif action.x is not None and action.y is not None:
            point = (action.x, action.y)
            prep = None
        else:
            return ActionResult("error", action, actor, f"{action.type} needs a point or a target")

        # Ground now, at the exact point, immediately before acting.
        try:
            grounding = await self.grounder.at_point(*point)
        except GroundingError as exc:
            return ActionResult("error", action, actor, str(exc), point=point)
        grounded = grounding.target
        if self.record_candidates:
            grounded = await self.grounder.with_candidates(grounding, self.app_fingerprint)

        if target is not None:
            same = await self.resolver.same(grounding.element, target.element)
            if not same or not (prep and prep.get("topmost")):
                return ActionResult(
                    "target_mismatch",
                    action,
                    actor,
                    f"under the point is <{grounded.tag}> {grounded.policy_label or ''}".strip(),
                    point=point,
                    grounded=grounded,
                )

        info = TargetInfo(label=grounded.policy_label, role=grounded.role)
        decision = self.engine.check(
            action, actor, replace(ctx, current_url=grounding.frame_url), info
        )
        if not decision.allowed:
            return _refused(action, actor, decision, point, grounded)

        # Nothing may read or change the page between this confirmation and the mouse.
        if not await self.grounder.confirm(grounding):
            return ActionResult(
                "target_mismatch",
                action,
                actor,
                "the page changed after grounding",
                point,
                grounded,
            )
        self._bind(actor, ctx)
        x, y = point
        mouse = self.page.mouse
        try:
            match action.type:
                case "click":
                    await mouse.click(x, y)
                case "double_click":
                    await mouse.click(x, y, click_count=2)
                case "move":
                    await mouse.move(x, y)
                case "scroll":
                    dx, dy = action.scroll or (0, 0)
                    await mouse.move(x, y)
                    await mouse.wheel(dx, dy)
                case "fill":
                    error = await self._fill(grounding, action.text or "")
                    if error:
                        return ActionResult(
                            "error", action, actor, error, point, grounded, decision
                        )
                case "select":
                    error = await self._select(grounding, action.option)
                    if error:
                        return ActionResult(
                            "error", action, actor, error, point, grounded, decision
                        )
                case _:
                    pass
        except PlaywrightError as exc:
            return ActionResult("error", action, actor, str(exc), point, grounded, decision)
        return ActionResult("done", action, actor, "", point, grounded, decision)

    async def _fill(self, grounding: Grounding, text: str) -> str | None:
        """Focus with a click, select what is there, delete it, then type key by key."""
        editable = element_fn("return EV.describe(el).editable;")
        if not await grounding.element.handle.evaluate(editable):
            return f"<{grounding.target.tag}> is not a text field"
        x, y = grounding.target.point
        await self.page.mouse.click(x, y)
        if await self.grounder.focused_backend_id() != grounding.backend_id:
            return "the click did not focus the field"
        had_text = await grounding.element.handle.evaluate(
            "e => { const had = e.value.length > 0; e.select(); return had; }"
        )
        if had_text:
            await self.page.keyboard.press("Delete")
        await self.page.keyboard.type(text)
        return None

    async def _select(self, grounding: Grounding, option: str | None) -> str | None:
        """Choose an option by the label a person reads. The label must match exactly one."""
        if grounding.target.tag != "select" or option is None:
            return "select needs a <select> and an option label"
        handle = grounding.element.handle
        matches = await handle.evaluate(
            element_fn("return Array.from(el.options).filter(o => EV.norm(o.text) === arg).length"),
            normalize_text(option),
        )
        if matches != 1:
            return f"{matches} options are labelled {option!r}"
        await handle.select_option(label=normalize_text(option))
        return None

    async def _keyboard(self, action: Action, actor: str, ctx: PolicyContext) -> ActionResult:
        if action.type == "type" and not _TYPEABLE.fullmatch(action.text or ""):
            return ActionResult("error", action, actor, "typed text must be printable ASCII")
        if action.type == "key" and not action.key:
            return ActionResult("error", action, actor, "key needs a key")
        # Ground the keyboard focus now: Enter on a focused "Post" button is a click on Post.
        grounded = await self.grounder.focus()
        info = None if grounded is None else TargetInfo(grounded.policy_label, grounded.role)
        decision = self.engine.check(action, actor, replace(ctx, current_url=self.page.url), info)
        if not decision.allowed:
            return _refused(action, actor, decision, None, grounded)
        self._bind(actor, ctx)
        try:
            if action.type == "type":
                await self.page.keyboard.type(action.text or "")
            else:
                assert action.key is not None
                await self.page.keyboard.press(playwright_key(action.key))
        except PlaywrightError as exc:
            return ActionResult("error", action, actor, str(exc), None, grounded, decision)
        return ActionResult("done", action, actor, "", None, grounded, decision)

    # --- predicates ---------------------------------------------------------------------------

    async def check(self, pred: AnyPredicate, ctx: PredicateContext | None = None) -> bool:
        return (await self.check_all([pred], ctx))[0]

    async def check_all(
        self, preds: Sequence[AnyPredicate], ctx: PredicateContext | None = None
    ) -> list[bool]:
        """One reading of the text on screen serves every predicate in the batch."""
        pctx = ctx or PredicateContext()
        boxes: list[TextBox] | None = None

        async def texts(scope: list[str]) -> list[str]:
            nonlocal boxes
            if boxes is None:
                boxes = await self.text_boxes()
            prefix = "/".join(scope)
            scoped = [b for b in boxes if not prefix or b.frame == prefix]
            return [normalize_text(b.text) for b in scoped] + group_lines(scoped)

        async def leaf(p: AnyPredicate) -> bool:
            match p:
                case TextPresent(text_present=args):
                    needle = normalize_text(args.text)
                    return any(needle in t for t in await texts(args.frame))
                case TextMatches(text_matches=args):
                    rx = re.compile(args.pattern)
                    return any(rx.search(t) for t in await texts(args.frame))
                case ElementPresent(element_present=args):
                    found = await self.resolve(args.target)
                    return isinstance(found, Unique) and not found.occluded
                case FieldValue(field_value=args):
                    spec = pctx.self_target if args.target == "self" else args.target
                    if spec is None:
                        return False
                    found = await self.resolve(spec)
                    if not isinstance(found, Unique):
                        return False
                    try:
                        value = await found.element.handle.evaluate(
                            element_fn("return EV.fieldValue(el);")
                        )
                    except PlaywrightError as exc:
                        if is_transient(exc):
                            return False
                        raise
                    return value == args.equals
                case UrlMatches(url_matches=args):
                    rx = re.compile(args.pattern)
                    return any(rx.search(f.url) for f in self.page.frames)
                case TitleMatches(title_matches=args):
                    return re.search(args.pattern, await self.page.title()) is not None
                case OutputPresent(output_present=name):
                    # Extracted and not null. An empty list is an answer ("no share accounts").
                    return pctx.outputs.get(name) is not None
                case _:
                    raise TypeError(f"not a leaf predicate: {type(p).__name__}")

        return [await evaluate(pred, leaf) for pred in preds]

    # --- extract ------------------------------------------------------------------------------

    async def extract(self, spec: ScalarExtract | TableSpec) -> RawValue:
        if isinstance(spec, ScalarExtract):
            found = await self.resolve(spec.target)
            if not isinstance(found, Unique):
                code = "ambiguous" if found.__class__.__name__ == "Ambiguous" else "not_found"
                raise ExtractError(code, f"cannot read {spec.target.description or 'target'}")
            if found.occluded:
                raise ExtractError("occluded", "the value is covered by another element")
            value = await found.element.handle.evaluate(element_fn("return EV.fieldValue(el);"))
            return normalize_text(str(value))
        return await self._table(spec)

    async def _table(self, spec: TableSpec) -> list[dict[str, str]]:
        if not spec.header_row:
            raise ExtractError("column_missing", "columns are found by header: header_row needed")
        keys = [label_key(t) for t in expand_aliases([spec.anchor_text], self.aliases)]
        results: list[dict[str, Any]] = []
        for _, frame in frames_in_scope(self.page, spec.frame):
            try:
                out = await frame.evaluate(page_fn("return EV.readTable(doc, arg);"), keys)
            except PlaywrightError as exc:
                if is_transient(exc):
                    continue
                raise
            if out.get("error") != "not_found":
                results.append(out)
        if not results:
            raise ExtractError("not_found", f"no table under {spec.anchor_text!r}")
        if len(results) > 1:
            raise ExtractError("ambiguous", f"{spec.anchor_text!r} appears in several frames")
        out = results[0]
        if "error" in out:
            raise ExtractError(out["error"], out.get("detail", ""))
        grid: list[list[str]] = out["grid"]
        if not grid:
            raise ExtractError("not_found", "the table is empty")
        return map_columns(Grid(header=grid[0], rows=grid[1:]), spec.columns, self.aliases)


def _loggable(g: GroundedTarget) -> dict[str, object]:
    """What a log line may say about a target. Names and text only for links and buttons: a
    click on a cell during discovery would otherwise write a member's name before it is a
    registered value the redactor knows to scrub."""
    control = g.role in ("link", "button") or g.tag == "a"
    return {
        "frame": g.frame,
        "tag": g.tag,
        "role": g.role,
        "name": g.name if control else None,
        "text": g.text if control else None,
    }


def _refused(
    action: Action,
    actor: str,
    decision: Decision,
    point: tuple[int, int] | None = None,
    grounded: GroundedTarget | None = None,
) -> ActionResult:
    status: ActStatus = {
        "block": "blocked",
        "require_approval": "needs_approval",
        "escalate": "escalate",
    }.get(decision.verdict, "blocked")  # type: ignore[assignment]
    return ActionResult(status, action, actor, decision.reason, point, grounded, decision)
