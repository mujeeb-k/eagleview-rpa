"""Surface-agnostic geometry over visible text: the `anchor` strategy, nearest-text descriptors and
line grouping. It reads only boxes (DOM ranges on the web, OCR or UI Automation on desktop), so a
desktop surface reuses it unchanged.

Font metrics differ between machines (CI has no Arial), so nothing here uses absolute pixel
positions: relations are overlaps and orderings, with a small tolerance."""

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from eagleview.schema.action import TextBox
from eagleview.schema.locators import AnchorLocator, Relation

# Two candidates whose distances differ by no more than this are a tie, and a tie is ambiguous.
TIE_PX = 2
# How far a control may start "inside" its anchor and still count as beside it.
EDGE_TOLERANCE_PX = 4


def normalize_text(text: str) -> str:
    """NFKC, non-breaking spaces to spaces, whitespace collapsed, ends trimmed."""
    text = unicodedata.normalize("NFKC", text).replace(" ", " ")
    return " ".join(text.split())


def label_key(text: str) -> str:
    """Labels compare case-insensitively and ignore a trailing colon: `Member #` matches
    `MEMBER #:` but not `Member #s`."""
    return normalize_text(text).rstrip(":").rstrip().casefold()


def expand_aliases(texts: Iterable[str], aliases: Mapping[str, Sequence[str]]) -> list[str]:
    """The texts plus every tenant alias declared for them, in order, without duplicates."""
    out: list[str] = []
    for text in texts:
        for candidate in (text, *aliases.get(text, ())):
            if candidate not in out:
                out.append(candidate)
    return out


@dataclass(frozen=True)
class Box:
    """A thing with geometry and an identity the caller can map back to an element."""

    key: int
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @classmethod
    def of(cls, key: int, box: TextBox) -> "Box":
        return cls(key, box.x, box.y, box.width, box.height)


def _overlap(a0: int, a1: int, b0: int, b1: int) -> int:
    return min(a1, b1) - max(a0, b0)


def _vertical_band(a: Box, b: Box) -> bool:
    """True when the boxes share a row: their vertical extents overlap by at least a third of the
    shorter one, so a tall control beside a short label still counts."""
    need = max(1, min(a.height, b.height) // 3)
    return _overlap(a.y, a.bottom, b.y, b.bottom) >= need


def _horizontal_band(a: Box, b: Box) -> bool:
    need = max(1, min(a.width, b.width) // 3)
    return _overlap(a.x, a.right, b.x, b.right) >= need


def relation_distance(anchor: Box, control: Box, relation: Relation) -> int | None:
    """Gap from anchor to control along the relation, or None when the control is not there."""
    tol = EDGE_TOLERANCE_PX
    match relation:
        case "right_of":
            ok = control.x >= anchor.right - tol and _vertical_band(anchor, control)
            gap = control.x - anchor.right
        case "left_of":
            ok = control.right <= anchor.x + tol and _vertical_band(anchor, control)
            gap = anchor.x - control.right
        case "below":
            ok = control.y >= anchor.bottom - tol and _horizontal_band(anchor, control)
            gap = control.y - anchor.bottom
        case "above":
            ok = control.bottom <= anchor.y + tol and _horizontal_band(anchor, control)
            gap = anchor.y - control.bottom
    return max(gap, 0) if ok else None


AnchorStatus = Literal["unique", "ambiguous", "not_found"]


@dataclass(frozen=True)
class AnchorResult:
    status: AnchorStatus
    keys: tuple[int, ...]  # the distinct control keys found (one when unique)
    detail: str = ""


def nearest_in_relation(
    anchor: Box, controls: Sequence[Box], relation: Relation
) -> tuple[Box | None, bool]:
    """The control nearest the anchor in the relation, and whether the nearest is a tie."""
    scored = sorted(
        (
            (d, c)
            for c in controls
            if c.key != anchor.key and (d := relation_distance(anchor, c, relation)) is not None
        ),
        key=lambda pair: pair[0],
    )
    if not scored:
        return None, False
    best_d, best = scored[0]
    tie = len(scored) > 1 and scored[1][0] - best_d <= TIE_PX and scored[1][1].key != best.key
    return best, tie


def resolve_anchor(
    locator: AnchorLocator,
    texts: Sequence[TextBox],
    controls: Sequence[Box],
    aliases: Mapping[str, Sequence[str]] | None = None,
) -> AnchorResult:
    """Every visible text box matching an anchor alias proposes the nearest control in the relation.
    One distinct control overall is unique. Two (two "Description" headers, or a tie beside one
    label) is ambiguous: replay never picks the first."""
    wanted = {label_key(t) for t in expand_aliases(locator.anchor_text, aliases or {})}
    anchors = [
        Box.of(-1 - i, t)
        for i, t in enumerate(texts)
        if t.source == "text" and label_key(t.text) in wanted
    ]
    if not anchors:
        return AnchorResult("not_found", (), "no visible anchor text")
    found: list[int] = []
    for anchor in anchors:
        best, tie = nearest_in_relation(anchor, controls, locator.relation)
        if tie:
            return AnchorResult("ambiguous", (), "two controls are equally near one anchor")
        if best is not None and best.key not in found:
            found.append(best.key)
    if not found:
        return AnchorResult("not_found", (), f"no {locator.control} {locator.relation} the anchor")
    if len(found) > 1:
        return AnchorResult("ambiguous", tuple(found), f"{len(found)} anchors each found a control")
    return AnchorResult("unique", tuple(found))


_LABEL_LIKE = re.compile(r"[A-Za-z]")


def nearest_text(target: Box, texts: Sequence[TextBox], direction: Relation) -> str | None:
    """The nearest document text in a direction from the target: what a person would read as its
    label. `left_of` finds text to the target's left (the target is right of it)."""
    flipped: dict[Relation, Relation] = {
        "left_of": "right_of",
        "right_of": "left_of",
        "above": "below",
        "below": "above",
    }
    candidates = [
        (Box.of(i, t), t)
        for i, t in enumerate(texts)
        if t.source == "text" and _LABEL_LIKE.search(t.text)
    ]
    best: tuple[int, str] | None = None
    for box, text in candidates:
        if (
            _overlap(box.x, box.right, target.x, target.right) > 0
            and _overlap(box.y, box.bottom, target.y, target.bottom) > 0
        ):
            continue  # the target's own text
        d = relation_distance(box, target, flipped[direction])
        if d is not None and (best is None or d < best[0]):
            best = (d, normalize_text(text.text))
    return best[1] if best else None


def group_lines(boxes: Sequence[TextBox]) -> list[str]:
    """Visible text as lines: boxes in the same frame whose vertical extents overlap join, left to
    right, with single spaces. Lets `text_present` match a phrase split across inline elements."""
    by_frame: dict[str | None, list[TextBox]] = {}
    for box in boxes:
        by_frame.setdefault(box.frame, []).append(box)
    lines: list[str] = []
    for frame_boxes in by_frame.values():
        rows: list[list[TextBox]] = []
        for box in sorted(frame_boxes, key=lambda b: (b.y, b.x)):
            for row in rows:
                if _vertical_band(Box.of(0, row[0]), Box.of(0, box)):
                    row.append(box)
                    break
            else:
                rows.append([box])
        for row in rows:
            lines.append(" ".join(normalize_text(b.text) for b in sorted(row, key=lambda b: b.x)))
    return lines


# How far below its label a labelled value may run when nothing below bounds it, in label heights.
VALUE_LINES = 4


def labelled_values(boxes: Sequence[TextBox], labels: Iterable[str]) -> list[tuple[str, TextBox]]:
    """The text shown beside each of `labels`, as (label, box) pairs.

    A label's value region starts at its right edge and runs:
    - across, up to the next label-like text on the label's row (a listed label, or text ending in
      a colon), so `Name:` stops at `Member #:` in a two-column layout;
    - down, to the next text in the label's own column, so a two-line address is covered, but at
      most VALUE_LINES label heights.
    Boxes whose centre falls in that region are values; label-like text is never a value. Labels
    are document text and must share a frame with their values."""
    keys = {label_key(text) for text in labels}

    def label_like(b: TextBox) -> bool:
        return b.source == "text" and (
            label_key(b.text) in keys or normalize_text(b.text).endswith(":")
        )

    out: list[tuple[str, TextBox]] = []
    for i, lab in enumerate(boxes):
        if lab.source != "text" or label_key(lab.text) not in keys:
            continue
        a = Box.of(i, lab)
        peers = [(Box.of(j, b), b) for j, b in enumerate(boxes) if j != i and b.frame == lab.frame]
        x_stops = [
            g.x
            for g, b in peers
            if label_like(b) and g.x >= a.right - EDGE_TOLERANCE_PX and _vertical_band(a, g)
        ]
        x_stop = min(x_stops, default=None)
        y_stops = [
            g.y
            for g, b in peers
            if b.source == "text" and g.y >= a.bottom - EDGE_TOLERANCE_PX and _horizontal_band(a, g)
        ]
        y_end = min([*y_stops, a.y + VALUE_LINES * max(a.height, 1)])
        for g, b in peers:
            if label_like(b) or g.x < a.right - EDGE_TOLERANCE_PX:
                continue
            if x_stop is not None and g.x >= x_stop:
                continue
            centre = g.y + g.height / 2
            if a.y - EDGE_TOLERANCE_PX <= centre < y_end:
                out.append((normalize_text(lab.text).rstrip(":").rstrip(), b))
    return out
