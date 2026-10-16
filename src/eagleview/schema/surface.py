"""What grounding reports about a pixel: the element under it, described the way a reviewer (and
every locator strategy) can recognize it again, plus the candidate locators that were verified to
resolve uniquely back to that same element."""

from pydantic import BaseModel, ConfigDict, Field

from eagleview.schema.locators import Locator

_STRICT = ConfigDict(extra="forbid", frozen=True)


class BBox(BaseModel):
    """Screenshot pixels, top-level viewport coordinates."""

    model_config = _STRICT
    x: int
    y: int
    width: int = Field(ge=0)
    height: int = Field(ge=0)

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


class GroundedTarget(BaseModel):
    model_config = _STRICT
    point: tuple[int, int]
    frame: list[str] = Field(default_factory=list[str])  # frame names from the top, e.g. ["main"]
    tag: str  # lowercase: "a", "input", "img", "font"
    input_type: str | None = None
    role: str | None = None  # accessibility role, when the element has a meaningful one
    name: str | None = None  # accessible name; empty names are reported as None
    label: str | None = None  # programmatic label: <label for>, aria-labelledby, aria-label
    text: str | None = None  # the element's own visible text, or a button's label
    row_header: str | None = None  # first cell of the table row the element sits in
    anchor_left: str | None = None  # nearest visible text to the left, in the same row band
    anchor_right: str | None = None
    anchor_above: str | None = None  # nearest visible text above, overlapping horizontally
    bbox: BBox
    # Ranked, and each one verified to resolve uniquely to this element at grounding time.
    candidates: list[Locator] = Field(default_factory=list[Locator])

    @property
    def policy_label(self) -> str | None:
        """What the policy engine reads to decide whether an activation is irreversible."""
        return self.name or self.text or self.label
