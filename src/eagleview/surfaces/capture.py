"""A screenshot together with the text boxes that say where to paint it.

A screenshot and a text-box read are two separate looks, and a page loading in between can make
them disagree. If text appeared after the boxes were read, the screenshot would show it unpainted.
So boxes are read on both sides of the screenshot and merged: anything on screen at either moment
is painted. Painting a little too much is the safe direction."""

from eagleview.schema.action import TextBox
from eagleview.surfaces.base import Observation, Surface


async def capture(surface: Surface) -> tuple[Observation, list[TextBox]]:
    first = await surface.text_boxes(every=True)
    observation = await surface.observe()
    second = await surface.text_boxes(every=True)
    seen: set[TextBox] = set()
    merged: list[TextBox] = []
    for box in [*first, *second]:
        if box not in seen:
            seen.add(box)
            merged.append(box)
    return observation, merged
