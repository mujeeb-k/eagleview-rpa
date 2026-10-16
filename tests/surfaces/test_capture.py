"""A screenshot's paint boxes come from both sides of it, so text appearing mid-load is painted."""

from typing import Any

from eagleview.schema.action import TextBox
from eagleview.surfaces.capture import capture


class Loading:
    """The page finishes loading between the screenshot and the second box read."""

    def __init__(self) -> None:
        self.reads = 0

    async def text_boxes(self, *, every: bool = False) -> list[TextBox]:
        self.reads += 1
        early = [TextBox(text="Member Search", x=0, y=0, width=10, height=10)]
        late = [TextBox(text="DOE, JANE Q", x=0, y=20, width=10, height=10)]
        return early if self.reads == 1 else early + late

    async def observe(self) -> Any:
        return "screenshot"


async def test_boxes_from_before_and_after_the_screenshot_are_merged() -> None:
    _, boxes = await capture(Loading())  # type: ignore[arg-type]
    assert [b.text for b in boxes] == ["Member Search", "DOE, JANE Q"]
