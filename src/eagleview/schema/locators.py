"""Locator strategies and target specs. Artifacts never mention Playwright: each strategy declares
which surfaces it can run on."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eagleview.schema.common import FramePath, SurfaceKind, TextList

_STRICT = ConfigDict(extra="forbid")

Control = Literal[
    "textbox", "button", "link", "select", "checkbox", "radio", "image", "text", "any"
]
Relation = Literal["right_of", "left_of", "below", "above"]


class A11yLocator(BaseModel):
    """Accessibility role plus accessible name."""

    model_config = _STRICT
    strategy: Literal["a11y"]
    role: str = Field(min_length=1)
    name: str


class AnchorLocator(BaseModel):
    """A control in a geometric relation to visible text. Works wherever text has geometry: DOM
    ranges on the web, OCR or UI Automation on desktop. `anchor_text` lists aliases."""

    model_config = _STRICT
    strategy: Literal["anchor"]
    anchor_text: TextList = Field(min_length=1)
    relation: Relation
    control: Control


class TextLocator(BaseModel):
    model_config = _STRICT
    strategy: Literal["text"]
    text: str = Field(min_length=1)
    exact: bool = True


class CssLocator(BaseModel):
    model_config = _STRICT
    strategy: Literal["css"]
    selector: str = Field(min_length=1)


class XPathLocator(BaseModel):
    model_config = _STRICT
    strategy: Literal["xpath"]
    expression: str = Field(min_length=1)


class PointLocator(BaseModel):
    """Last resort: a pixel. Valid only while the live app fingerprint equals the one recorded."""

    model_config = _STRICT
    strategy: Literal["point"]
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    bound_to_fingerprint: str = Field(min_length=1)


Locator = Annotated[
    A11yLocator | AnchorLocator | TextLocator | CssLocator | XPathLocator | PointLocator,
    Field(discriminator="strategy"),
]

Strategy = Literal["a11y", "anchor", "text", "css", "xpath", "point"]

# `legacy_web` is still a browser page, so DOM strategies apply to it; only desktop lacks a DOM.
STRATEGY_SURFACES: dict[Strategy, frozenset[SurfaceKind]] = {
    "a11y": frozenset({"web", "legacy_web", "desktop"}),
    "anchor": frozenset({"web", "legacy_web", "desktop"}),
    "text": frozenset({"web", "legacy_web", "desktop"}),
    "css": frozenset({"web", "legacy_web"}),
    "xpath": frozenset({"web", "legacy_web"}),
    "point": frozenset({"web", "legacy_web", "desktop"}),
}


def strategy_applies(strategy: Strategy, surface: SurfaceKind) -> bool:
    return surface in STRATEGY_SURFACES[strategy]


class TargetSpec(BaseModel):
    """What a step acts on: ranked locators, where replay takes the first unique match."""

    model_config = _STRICT
    description: str | None = None
    frame: FramePath = Field(default_factory=list[str])
    locators: list[Locator] = Field(min_length=1)
    # Derived when omitted: fewer than two non-point strategies means fragile, so a reviewer sees
    # it. It may be set true by hand; it may not be set false when the rule says otherwise.
    fragile: bool | None = None

    @model_validator(mode="after")
    def _derive_fragile(self) -> Self:
        non_point = {loc.strategy for loc in self.locators if loc.strategy != "point"}
        derived = len(non_point) < 2
        if self.fragile is None:
            self.fragile = derived
        elif derived and not self.fragile:
            raise ValueError(
                "fragile: false needs at least two distinct non-point locator strategies"
            )
        return self
