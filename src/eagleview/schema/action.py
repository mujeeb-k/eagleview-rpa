"""The UI action every actor sends through `Surface.act()`, and who sent it."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# automation: the replay engine. llm: the discovery model. harness: login macros and setup.
# operator:<name>: a human in the console.
Actor = Annotated[
    str, StringConstraints(pattern=r"^(automation|llm|harness|system|operator:[a-z0-9_.-]{1,64})$")
]


def is_operator(actor: str) -> bool:
    return actor.startswith("operator:")


ActionType = Literal[
    "click",
    "double_click",
    "right_click",
    "move",
    "scroll",
    "type",
    "key",
    "fill",
    "select",
    "navigate",
    "drag",
    "upload",
    "download",
    "screenshot",
    "zoom",
    "wait",
]

# Observing changes nothing, so the policy never blocks it.
OBSERVE_ACTIONS: frozenset[str] = frozenset({"screenshot", "zoom", "wait"})


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: ActionType
    x: int | None = Field(default=None, ge=0)
    y: int | None = Field(default=None, ge=0)
    text: str | None = None  # type / fill value
    key: str | None = None  # xdotool-style: "Return", "Tab", "ctrl+a"
    url: str | None = None  # navigate
    option: str | None = None  # select, by label
    region: tuple[int, int, int, int] | None = None  # zoom: x0, y0, x1, y1
    duration_ms: int | None = Field(default=None, ge=0)


class TextBox(BaseModel):
    """Visible text and its geometry in screenshot pixels: DOM ranges on web, OCR/UIA on desktop."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str
    x: int
    y: int
    width: int = Field(ge=0)
    height: int = Field(ge=0)
    frame: str | None = None
