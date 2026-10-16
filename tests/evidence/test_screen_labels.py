"""Screen-level redaction without a browser: which text counts as the value beside a sensitive
label, and the writer painting and scrubbing it."""

import io
from pathlib import Path

from PIL import Image

from eagleview.evidence.writer import EvidenceWriter
from eagleview.safety.redaction import Redactor
from eagleview.schema.action import TextBox
from eagleview.schema.app_profile import ScreenLabels
from eagleview.surfaces.geometry import labelled_values


def box(text: str, x: int, y: int, w: int = 60, h: int = 14, **kw: object) -> TextBox:
    return TextBox(text=text, x=x, y=y, width=w, height=h, frame="main", **kw)  # type: ignore[arg-type]


# Member Detail as laid out on screen: two label columns, a two-line address, notes, then a table.
DETAIL = [
    box("Member Detail", 10, 10, 140, 18),
    box("Name:", 10, 50, 40),
    box("DOE, JANE Q", 70, 50, 120),
    box("Member #:", 330, 50, 70),
    box("10482", 430, 50, 40),
    box("Phone:", 10, 72, 45),
    box("(555) 010-1234", 70, 72, 110),
    box("Email:", 330, 72, 45),
    box("jane@example.test", 430, 72, 140),
    box("Address:", 10, 94, 60),
    box("12 Harbor Way Apt 3", 75, 94, 150),
    box("Port Lantern, ME 04011", 75, 110, 160),
    box("Member Since:", 330, 94, 95),
    box("02/10/2011", 430, 94, 80),
    box("Notes:", 10, 132, 45),
    box("Prefers phone contact.", 75, 132, 170),
    box("Share Accounts", 10, 160, 120, 16),
    box("Sfx", 10, 182, 30),
    box("Description", 75, 182, 90),
    box("Regular Share", 75, 204, 100),
]
LABELS = ["Name", "Member #", "Phone", "Email", "Address", "Notes"]


def values(boxes: list[TextBox], labels: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for label, b in labelled_values(boxes, labels):
        out.setdefault(label, []).append(b.text)
    return out


def test_values_beside_labels_stop_at_the_next_label_and_the_next_row() -> None:
    assert values(DETAIL, LABELS) == {
        "Name": ["DOE, JANE Q"],
        "Member #": ["10482"],
        "Phone": ["(555) 010-1234"],
        "Email": ["jane@example.test"],
        "Address": ["12 Harbor Way Apt 3", "Port Lantern, ME 04011"],
        "Notes": ["Prefers phone contact."],
    }


def test_unlisted_labels_and_tables_are_left_alone() -> None:
    painted = {b.text for _, b in labelled_values(DETAIL, LABELS)}
    for kept in ["02/10/2011", "Member Since:", "Share Accounts", "Description", "Regular Share"]:
        assert kept not in painted


def test_a_typed_field_value_beside_a_label_counts() -> None:
    boxes = [box("Member #", 10, 50, 60), box("10482", 80, 48, 90, 18, source="control")]
    assert values(boxes, ["Member #"]) == {"Member #": ["10482"]}


def test_labels_and_values_must_share_a_frame() -> None:
    other = TextBox(text="DOE, JANE Q", x=70, y=50, width=120, height=14, frame="nav")
    assert labelled_values([box("Name:", 10, 50, 40), other], ["Name"]) == []


def test_a_value_with_nothing_below_runs_at_most_a_few_lines() -> None:
    boxes = [box("Notes:", 10, 100, 45), box("line one", 70, 100), box("far below", 70, 400)]
    assert values(boxes, ["Notes"]) == {"Notes": ["line one"]}


# --- the writer -----------------------------------------------------------------------------------


def white_png(w: int = 640, h: int = 240) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(out, format="PNG")
    return out.getvalue()


def painted(path: Path, b: TextBox) -> bool:
    """Every pixel of the box is black: filled, not merely crossed by a glyph."""
    with Image.open(path) as image:
        crop = image.convert("RGB").crop((b.x, b.y, b.x + b.width, b.y + b.height))
        return all(px == (0, 0, 0) for px in crop.get_flattened_data())


def writer(tmp_path: Path, rules: list[ScreenLabels]) -> EvidenceWriter:
    w = EvidenceWriter(tmp_path, "r_screen", Redactor())
    w.use_screen_labels(rules)
    return w


def test_writer_paints_labelled_values_and_scrubs_them_from_dom(tmp_path: Path) -> None:
    w = writer(tmp_path, [ScreenLabels(screen="Member Detail", labels=LABELS)])
    path = w.screenshot(1, "after", white_png(), text_boxes=DETAIL)
    by_text = {b.text: b for b in DETAIL}
    for text in ["DOE, JANE Q", "12 Harbor Way Apt 3", "Port Lantern, ME 04011"]:
        assert painted(path, by_text[text]), text
    for text in ["Member Since:", "02/10/2011", "Regular Share", "Name:"]:
        assert not painted(path, by_text[text]), text
    dom = w.dom_snapshot("failure", "<td>12 Harbor Way Apt 3<br>Port Lantern, ME 04011</td>")
    written = dom.read_text()
    assert "Harbor" not in written and "Port Lantern" not in written


def test_a_rule_for_another_screen_does_not_apply(tmp_path: Path) -> None:
    w = writer(tmp_path, [ScreenLabels(screen="Review - Open Share", labels=["Name"])])
    path = w.screenshot(1, "after", white_png(), text_boxes=DETAIL)
    assert not painted(path, DETAIL[2])


def test_a_rule_without_a_screen_applies_everywhere(tmp_path: Path) -> None:
    w = writer(tmp_path, [ScreenLabels(labels=["Name"])])
    path = w.screenshot(1, "after", white_png(), text_boxes=DETAIL)
    assert painted(path, DETAIL[2])


def test_tenant_aliases_extend_the_labels(tmp_path: Path) -> None:
    w = EvidenceWriter(tmp_path, "r_alias", Redactor())
    w.use_screen_labels([ScreenLabels(labels=["Member #"])], {"Member #": ["Acct Holder No."]})
    boxes = [box("Acct Holder No.", 10, 50, 110), box("2210236", 130, 50)]
    path = w.screenshot(1, "after", white_png(), text_boxes=boxes)
    assert painted(path, boxes[1])


def test_a_modal_over_the_screen_is_never_the_value_of_a_label_beneath_it() -> None:
    covered = [b.model_copy(update={"covered": True}) for b in DETAIL]
    modal = box("Drawer 04 is over its cash limit", 60, 48, 300)  # on top, beside "Name:"
    found = values([*covered, modal], ["Name"])
    assert found == {"Name": ["DOE, JANE Q"]}
