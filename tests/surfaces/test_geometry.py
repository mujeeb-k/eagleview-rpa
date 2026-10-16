"""Anchor geometry, nearest-text descriptors and line grouping: pure, no browser."""

from typing import Any

import pytest

from eagleview.schema.action import TextBox
from eagleview.schema.locators import AnchorLocator
from eagleview.schema.predicates import AnyPredicate
from eagleview.surfaces.geometry import (
    Box,
    expand_aliases,
    group_lines,
    label_key,
    nearest_text,
    resolve_anchor,
)
from eagleview.surfaces.predicates import describe, evaluate
from tests.surfaces.helpers import pred


def text(t: str, x: int, y: int, w: int = 60, h: int = 14, source: str = "text") -> TextBox:
    return TextBox(text=t, x=x, y=y, width=w, height=h, frame="main", source=source)  # type: ignore[arg-type]


def anchor(*texts: str, relation: str = "right_of", control: str = "textbox") -> AnchorLocator:
    return AnchorLocator.model_validate(
        {"strategy": "anchor", "anchor_text": list(texts), "relation": relation, "control": control}
    )


# A table-layout search form: label | field | GO, and a second row below.
FORM = [text("Member #", 10, 100), text("Last Name", 10, 130)]
FIELDS = [Box(0, 90, 98, 120, 20), Box(1, 90, 128, 160, 20)]


def test_label_key() -> None:
    assert label_key("Member #:") == label_key(" member # ") == "member #"
    assert label_key("Member #s") != label_key("Member #")


def test_right_of_picks_the_field_on_the_labels_row() -> None:
    assert resolve_anchor(anchor("Member #"), FORM, FIELDS).keys == (0,)
    assert resolve_anchor(anchor("Last Name"), FORM, FIELDS).keys == (1,)


def test_aliases_from_the_locator_and_the_tenant() -> None:
    relabeled = [text("Acct Holder No.", 10, 100)]
    assert (
        resolve_anchor(anchor("Member #", "Acct Holder No."), relabeled, FIELDS).status == "unique"
    )
    tenant = {"Member #": ["Acct Holder No."]}
    assert resolve_anchor(anchor("Member #"), relabeled, FIELDS, tenant).keys == (0,)
    assert resolve_anchor(anchor("Member #"), relabeled, FIELDS).status == "not_found"
    assert expand_aliases(["a", "b"], {"a": ["x", "b"]}) == ["a", "x", "b"]


def test_two_anchors_finding_two_controls_is_ambiguous() -> None:
    # The same header over two tables: never pick the first.
    texts = [text("Description", 10, 100), text("Description", 10, 300)]
    cells = [Box(0, 10, 120, 80, 14), Box(1, 10, 320, 80, 14)]
    result = resolve_anchor(anchor("Description", relation="below", control="text"), texts, cells)
    assert result.status == "ambiguous"
    assert set(result.keys) == {0, 1}


def test_a_tie_beside_one_anchor_is_ambiguous() -> None:
    texts = [text("Amount", 100, 100)]
    twins = [Box(0, 90, 120, 40, 14), Box(1, 130, 121, 40, 14)]  # both directly below, 1 px apart
    result = resolve_anchor(anchor("Amount", relation="below", control="text"), texts, twins)
    assert result.status == "ambiguous"


def test_an_exact_tie_is_ambiguous() -> None:
    texts = [text("Actions", 100, 100, 100)]
    buttons = [Box(0, 100, 130, 40, 20), Box(1, 150, 130, 40, 20)]  # side by side, same gap
    result = resolve_anchor(anchor("Actions", relation="below", control="button"), texts, buttons)
    assert result.status == "ambiguous"


def test_control_text_is_never_an_anchor() -> None:
    # A button labelled "Member #" is drawn by a control; it is not a label on the page.
    texts = [text("Member #", 10, 100, source="control")]
    assert resolve_anchor(anchor("Member #"), texts, FIELDS).status == "not_found"


@pytest.mark.parametrize(
    ("relation", "expected"),
    [("right_of", 2), ("left_of", 0), ("below", 3), ("above", 1)],
)
def test_each_relation(relation: str, expected: int) -> None:
    centre = [text("X", 200, 200, 20, 14)]
    around = [
        Box(0, 140, 200, 30, 14),  # left
        Box(1, 195, 150, 30, 14),  # above
        Box(2, 240, 199, 30, 14),  # right
        Box(3, 195, 240, 30, 14),  # below
    ]
    result = resolve_anchor(anchor("X", relation=relation, control="any"), centre, around)
    assert result.keys == (expected,)


def test_tolerates_font_metric_drift() -> None:
    # CI fonts differ: a field 3 px taller and offset still shares the label's row.
    texts = [text("Nickname", 10, 100, 70, 14)]
    fields = [Box(0, 78, 96, 150, 23)]
    assert resolve_anchor(anchor("Nickname"), texts, fields).keys == (0,)


def test_nearest_text_descriptors() -> None:
    texts = [*FORM, text("Search Criteria", 10, 70, 120), text("(optional)", 220, 100)]
    field = Box(0, 90, 98, 120, 20)
    assert nearest_text(field, texts, "left_of") == "Member #"
    assert nearest_text(field, texts, "right_of") == "(optional)"
    assert nearest_text(field, texts, "above") == "Search Criteria"


def test_group_lines_joins_a_row_left_to_right() -> None:
    boxes = [text("#10482", 120, 50), text("Member:", 10, 51), text("Jane Doe", 60, 49)]
    assert group_lines(boxes) == ["Member: Jane Doe #10482"]


async def test_combinators_short_circuit() -> None:
    seen: list[str] = []

    async def leaf(p: AnyPredicate) -> bool:
        dumped: dict[str, Any] = p.model_dump(mode="json")
        ((_, args),) = dumped.items()
        seen.append(args["text"])
        return args["text"] == "yes"

    yes = {"text_present": {"text": "yes"}}
    no = {"text_present": {"text": "no"}}
    assert await evaluate(pred({"any": [yes, no]}), leaf) is True
    assert seen == ["yes"]
    assert await evaluate(pred({"all": [no, yes]}), leaf) is False
    assert await evaluate(pred({"not": no}), leaf) is True
    assert await evaluate(pred({"all": [yes, {"not": {"any": [no, no]}}]}), leaf) is True
    assert "not(" in describe(pred({"not": no}))
