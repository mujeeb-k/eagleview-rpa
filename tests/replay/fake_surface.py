"""A scripted Surface for engine unit tests. The screen is a set of visible strings, and each act()
runs a script, keyed by the target's label or else the step id, that changes it. No browser, so
the tests are fast and exact."""

import hashlib
import io
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from eagleview.safety.policy_engine import ApprovalToken, PolicyContext
from eagleview.schema.action import Action, TextBox
from eagleview.schema.capability import ScalarExtract, TableSpec
from eagleview.schema.common import SurfaceKind
from eagleview.schema.locators import TargetSpec
from eagleview.schema.predicates import (
    AnyPredicate,
    OutputPresent,
    TextMatches,
    TextPresent,
)
from eagleview.schema.surface import GroundedTarget
from eagleview.surfaces.base import (
    ActionResult,
    BlockedRequest,
    Element,
    HolderGuard,
    NotHolder,
    Observation,
    PredicateContext,
    Resolution,
    Surface,
    SurfaceOpener,
    Unique,
)
from eagleview.surfaces.extract import RawValue
from eagleview.surfaces.predicates import evaluate

FOOTER = "Ironbridge Teller v7.2.3 — TRAINING REGION"


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(out, format="PNG")
    return out.getvalue()


Script = Callable[["FakeSurface", Action], None]


@dataclass
class FakeSurface:
    holder: HolderGuard
    scripts: dict[str, Script] = field(default_factory=dict[str, Script])
    texts: set[str] = field(default_factory=lambda: {FOOTER})
    kind: SurfaceKind = "legacy_web"
    app_fingerprint: str | None = None
    acts: list[tuple[str | None, str]] = field(default_factory=list[tuple[str | None, str]])
    approvals: set[ApprovalToken] = field(default_factory=set[ApprovalToken])
    navigations: int = 0
    _png: bytes = field(default_factory=_png)

    def show(self, *texts: str) -> None:
        self.texts |= set(texts)
        self.navigations += 1

    def hide(self, *texts: str) -> None:
        self.texts -= set(texts)

    async def observe(self) -> Observation:
        digest = hashlib.sha256("|".join(sorted(self.texts)).encode()).hexdigest()
        return Observation(self._png, 4, 4, f"sha256:{digest}", "fake://", "fake", (), ())

    async def act(
        self,
        action: Action,
        actor: str,
        epoch: int,
        *,
        target: Unique | None = None,
        step_id: str | None = None,
    ) -> ActionResult:
        try:
            self.holder.assert_holder(actor, epoch)
        except NotHolder as exc:
            return ActionResult("not_holder", action, actor, str(exc))
        self.acts.append((step_id, action.type))
        # A script is keyed by the clicked target's label, else by the step id.
        script = self.scripts.get(_label(target)) or self.scripts.get(step_id or "")
        if script is not None:
            script(self, action)
        return ActionResult("done", action, actor)

    async def ground(self, x: int, y: int) -> GroundedTarget:
        raise NotImplementedError

    async def resolve(self, target: TargetSpec) -> Resolution:
        return Unique(Element(target, (), "x"), target.locators[0].strategy, 0, agreement=None)

    async def check(self, pred: AnyPredicate, ctx: PredicateContext | None = None) -> bool:
        return (await self.check_all([pred], ctx))[0]

    async def check_all(
        self, preds: Sequence[AnyPredicate], ctx: PredicateContext | None = None
    ) -> list[bool]:
        pctx = ctx or PredicateContext()

        async def leaf(p: AnyPredicate) -> bool:
            match p:
                case TextPresent(text_present=args):
                    return any(args.text in t for t in self.texts)
                case TextMatches(text_matches=args):
                    import re

                    return any(re.search(args.pattern, t) for t in self.texts)
                case OutputPresent(output_present=name):
                    return pctx.outputs.get(name) is not None
                case _:
                    return True

        return [await evaluate(p, leaf) for p in preds]

    async def extract(self, spec: ScalarExtract | TableSpec) -> RawValue:
        return "42"

    async def text_boxes(self, *, every: bool = False) -> list[TextBox]:
        return [TextBox(text=t, x=0, y=0, width=1, height=1) for t in sorted(self.texts)]

    async def dom_snapshot(self) -> str:
        return "<html>" + " ".join(sorted(self.texts)) + "</html>"

    def navigation_count(self) -> int:
        return self.navigations

    def drain_blocked(self) -> list[BlockedRequest]:
        return []

    def grant_approval(self, token: ApprovalToken) -> None:
        self.approvals.add(token)


def _label(target: Unique | None) -> str:
    if target is None:
        return ""
    spec: TargetSpec = target.element.handle
    first = spec.locators[0]
    return str(getattr(first, "name", None) or getattr(first, "text", None) or "")


def signs_on(surface: FakeSurface, action: Action) -> None:
    surface.show("Teller: teller01")


def opener_for(scripts: dict[str, Script], made: list[FakeSurface]) -> SurfaceOpener:
    def open_surface(holder: HolderGuard, ctx: PolicyContext) -> AbstractAsyncContextManager[Any]:
        @asynccontextmanager
        async def opened() -> AsyncIterator[Surface]:
            surface = FakeSurface(holder, {"login.sign_on": signs_on, **scripts})
            made.append(surface)
            yield surface

        return opened()

    return open_surface
