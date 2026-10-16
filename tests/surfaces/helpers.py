"""Test helpers: target specs written the way artifacts write them, and a polling wait."""

import asyncio
from typing import Any

from pydantic import TypeAdapter

from eagleview.schema.action import Action
from eagleview.schema.locators import TargetSpec
from eagleview.schema.predicates import AnyPredicate, Predicate
from eagleview.surfaces.base import ActionResult, Unique
from eagleview.surfaces.web.playwright_surface import PlaywrightSurface
from tests.support.bank import Bank

EPOCH = 1
_PRED: TypeAdapter[AnyPredicate] = TypeAdapter(Predicate)


def spec(*locators: dict[str, Any], frame: list[str] | None = None, desc: str = "") -> TargetSpec:
    return TargetSpec.model_validate(
        {"description": desc or None, "frame": frame or [], "locators": list(locators)}
    )


def pred(data: dict[str, Any]) -> AnyPredicate:
    return _PRED.validate_python(data)


async def wait_until(
    surface: PlaywrightSurface, predicate: dict[str, Any], timeout_s: float = 5.0
) -> None:
    """Poll a predicate. Tests only; replay's settle loop is P4's."""
    p = pred(predicate)
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not await surface.check(p):
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out waiting for {predicate}")
        await asyncio.sleep(0.1)


async def resolved(surface: PlaywrightSurface, target: TargetSpec) -> Unique:
    result = await surface.resolve(target)
    assert isinstance(result, Unique), result
    return result


async def act_on(
    surface: PlaywrightSurface,
    target: TargetSpec,
    action: str = "click",
    actor: str = "automation",
    step_id: str | None = None,
    **fields: Any,
) -> ActionResult:
    unique = await resolved(surface, target)
    return await surface.act(
        Action(type=action, **fields),  # type: ignore[arg-type]
        actor,
        EPOCH,
        target=unique,
        step_id=step_id,
    )


# --- Lantern Bay targets, as an artifact would hold them ---------------------------------------

NAV_INQUIRY = spec(
    {"strategy": "a11y", "role": "link", "name": "Member Inquiry"},
    {"strategy": "text", "text": "Member Inquiry"},
    {"strategy": "css", "selector": "a[href='/inq']"},
    frame=["nav"],
    desc="'Member Inquiry' link",
)
MEMBER_FIELD = spec(
    {
        "strategy": "anchor",
        "anchor_text": ["Member #"],
        "relation": "right_of",
        "control": "textbox",
    },
    {"strategy": "css", "selector": "input[name='txtA']"},
    frame=["main"],
    desc="member number field",
)
GO_IMAGE = spec(
    {"strategy": "anchor", "anchor_text": ["Member #"], "relation": "right_of", "control": "image"},
    {"strategy": "css", "selector": "img[src*='go']"},
    frame=["main"],
    desc="GO image button",
)


def button(label: str) -> TargetSpec:
    return spec(
        {"strategy": "a11y", "role": "button", "name": label},
        {"strategy": "text", "text": label},
        {"strategy": "css", "selector": f"input[value='{label}']"},
        frame=["main"],
        desc=f"'{label}' button",
    )


async def to_inquiry(surface: PlaywrightSurface) -> None:
    result = await act_on(surface, NAV_INQUIRY)
    assert result.ok, result
    await wait_until(surface, {"text_present": {"frame": "main", "text": "Member Search"}})


async def to_member(surface: PlaywrightSurface, bank: Bank, member: str) -> None:
    """Member Inquiry, type the number, GO: all through Surface.act, as replay will."""
    await to_inquiry(surface)
    result = await act_on(surface, MEMBER_FIELD, "fill", text=member)
    assert result.ok, result
    go = GO_IMAGE if bank.tenant.search_button == "image" else button("Search")
    result = await act_on(surface, go)
    assert result.ok, result
    await wait_until(surface, {"text_present": {"frame": "main", "text": "Member Detail"}})
