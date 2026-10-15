"""The evidence writer: layout, redaction of every kind of file, and the limits of redaction."""

import io
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image, PngImagePlugin
from pydantic import ValidationError

from eagleview.artifacts.yaml_io import load_capability, load_policy
from eagleview.evidence.writer import EvidenceWriter
from eagleview.safety.redaction import Redactor
from eagleview.safety.secrets import SecretResolver
from eagleview.schema.action import TextBox
from eagleview.schema.result import RunResult
from tests.fixtures import CAPABILITY, POLICY


def make_redactor() -> Redactor:
    redactor = Redactor(load_policy(POLICY).sensitivity_patterns)
    redactor.use_capability(load_capability(CAPABILITY))
    return redactor


@pytest.fixture
def writer(tmp_path: Path) -> EvidenceWriter:
    return EvidenceWriter(tmp_path / "runs", "r_test", make_redactor())


def lines(writer: EvidenceWriter) -> list[dict[str, object]]:
    return [json.loads(line) for line in writer.events_path.read_text().splitlines()]


def test_layout_and_sequence(writer: EvidenceWriter) -> None:
    writer.event("run.start", "automation", capability="member.lookup_share_balances@1.0.0")
    writer.event("step.act", "automation", step_id="enter_member_id")
    writer.summary({"status": "success"})
    events = lines(writer)
    assert [e["seq"] for e in events] == [0, 1]
    assert all(e["run_id"] == "r_test" for e in events)
    assert list(events[0])[:5] == ["ts", "run_id", "seq", "actor", "kind"]
    assert (writer.run_dir / "summary.json").exists()
    assert writer.run_dir == writer.run_dir.parent / "r_test"


def test_structural_fields_and_ordinary_dates_survive(writer: EvidenceWriter) -> None:
    writer.event(
        "step.observe",
        "automation",
        screen="10/14/2026 S01 POS DEBIT GROCERY 45.00- | Member Since: 03/02/2011",
        posted_at="2026-10-15T09:30:00+00:00",
        txn="Transaction 7200001 posted 10/15/2026",
    )
    event = lines(writer)[0]
    datetime.fromisoformat(str(event["ts"]))  # the timestamp is intact and parseable
    assert event["screen"] == "10/14/2026 S01 POS DEBIT GROCERY 45.00- | Member Since: 03/02/2011"
    assert event["posted_at"] == "2026-10-15T09:30:00+00:00"
    assert event["txn"] == "Transaction 7200001 posted 10/15/2026"


def test_labelled_dob_ssn_and_names_are_scrubbed(writer: EvidenceWriter) -> None:
    writer.redactor.register_outputs({"member_name": "DOE, JANE Q"})
    writer.event(
        "step.observe",
        "automation",
        screen="Name: DOE, JANE Q  SSN 123-45-6789  DOB: 04/12/1961  posted 10/14/2026",
        note="Jane Doe asked about her holiday club",
    )
    raw = writer.events_path.read_text()
    for leaked in ("DOE", "JANE", "Jane", "Doe", "123-45-6789", "04/12/1961"):
        assert leaked not in raw
    assert "posted 10/14/2026" in raw  # the transaction date survives next to the scrubbed DOB


def test_event_fields_cannot_override_structure(writer: EvidenceWriter) -> None:
    with pytest.raises(ValueError, match="may not override"):
        writer.event("x", "automation", seq=99)
    with pytest.raises(ValidationError):
        writer.event("Not A Kind", "automation")
    with pytest.raises(ValidationError):
        writer.event("x", "somebody")


def test_secret_in_a_fill_event_never_reaches_disk(writer: EvidenceWriter) -> None:
    resolver = SecretResolver(
        {"LANTERN_BAY_TELLER_PASSWORD": "q9!Xv-Training"}, writer.redactor.register_secret
    )
    password = resolver.resolve("secret://lantern_bay_teller_password")
    writer.event(
        "login.fill", "harness", step_id="enter_password", value=password, echo=f"pw={password}"
    )
    raw = writer.events_path.read_text()
    assert "q9!Xv-Training" not in raw
    assert lines(writer)[0]["value"] == "[secret]"


def test_unicode_is_redacted_before_json_escaping(writer: EvidenceWriter) -> None:
    writer.redactor.register_outputs({"member_name": "PEÑA, JOSÉ A"})
    writer.event("step.observe", "automation", text="Member José Peña", data={"José": 1})
    writer.write_json("result", {"note": "PEÑA, JOSÉ A"})
    for path in (writer.events_path, writer.run_dir / "result.json"):
        raw = path.read_text()
        for leaked in ("José", "Peña", "JOSÉ", "PEÑA", "Jos\\u00e9", "Pe\\u00f1a"):
            assert leaked not in raw, (path, leaked)


def test_summary_applies_typed_masking_to_run_results(writer: EvidenceWriter) -> None:
    result = RunResult(
        run_id="r_test",
        capability="member.lookup_share_balances@1.0.0",
        tenant="lantern-bay",
        status="success",
        outputs={
            "member_name": "DOE, JANE Q",
            "share_accounts": [
                {
                    "suffix": "S00",
                    "description": "Regular Share",
                    "balance": Decimal("1234.56"),
                    "available": Decimal("1200.00"),
                }
            ],
        },
    )
    data = json.loads(writer.summary(result).read_text())
    assert data["outputs"]["member_name"] == "D. J. Q."
    assert data["outputs"]["share_accounts"][0]["balance"] == "1234.56"


def _png(width: int = 200, height: int = 100, metadata: str | None = None) -> bytes:
    image = Image.new("RGB", (width, height), (255, 255, 255))
    out = io.BytesIO()
    info = PngImagePlugin.PngInfo()
    if metadata is not None:
        info.add_text("Comment", metadata)
    image.save(out, format="PNG", pnginfo=info)
    return out.getvalue()


def test_screenshots_paint_over_sensitive_boxes_only(writer: EvidenceWriter) -> None:
    writer.redactor.register_inputs({"member_id": "10482"})
    boxes = [
        TextBox(text="10482", x=20, y=20, width=40, height=12),
        TextBox(text="SSN 123-45-6789", x=20, y=50, width=80, height=12),
        TextBox(text="Member Search", x=120, y=20, width=60, height=12),
        TextBox(text="DOB: 01/02/1970", x=190, y=90, width=50, height=30),  # runs off the edge
    ]
    path = writer.screenshot(1, "before", _png(metadata="member 10482"), text_boxes=boxes)
    assert path.name == "0001_before.png" and path.parent.name == "steps"
    with Image.open(path) as image:
        assert image.getpixel((40, 26)) == (0, 0, 0)
        assert image.getpixel((60, 56)) == (0, 0, 0)
        assert image.getpixel((150, 26)) == (255, 255, 255)
        assert image.getpixel((195, 95)) == (0, 0, 0)
        assert image.getpixel((5, 5)) == (255, 255, 255)
        assert "Comment" not in image.info  # metadata chunks are dropped
    assert b"10482" not in path.read_bytes()


def test_screenshot_requires_text_boxes(writer: EvidenceWriter) -> None:
    with pytest.raises(TypeError):
        writer.screenshot(1, "after", _png())  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        writer.screenshot(10000, "after", _png(), text_boxes=[])


def test_dom_snapshot_scrubs_attributes_and_escaped_text(writer: EvidenceWriter) -> None:
    writer.redactor.register_inputs({"member_id": "10482"})
    writer.redactor.register_outputs({"member_name": "O'BRIEN, PAT & CO"})
    html = (
        '<input type="text" name="txtA" value="10482">'
        '<input type="hidden" name="m" value="10482">'
        "<td>O&#x27;BRIEN, PAT &amp; CO</td><td>SSN 123-45-6789</td><td>Posted 10/14/2026</td>"
    )
    raw = writer.dom_snapshot("failure", html).read_text()
    assert "10482" not in raw and "BRIEN" not in raw and "123-45-6789" not in raw
    assert "Posted 10/14/2026" in raw
    assert writer.run_dir / "dom" / "failure.redacted.html"


def test_names_and_run_ids_are_checked(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        EvidenceWriter(tmp_path, "../escape", make_redactor())
    with pytest.raises(ValidationError):
        EvidenceWriter(tmp_path, "r_a/b", make_redactor())
    writer = EvidenceWriter(tmp_path, "r_ok", make_redactor())
    with pytest.raises(FileExistsError):
        EvidenceWriter(tmp_path, "r_ok", make_redactor())
    for bad in ("../x", "a/b", "", "Summary"):
        with pytest.raises(ValueError):
            writer.write_json(bad, {})


def test_top_level_event_keys_are_redacted(writer: EvidenceWriter) -> None:
    """Regression: found by the property test. Keys beside the structural fields were copied."""
    writer.redactor.register_inputs({"member_id": "10482"})
    writer.event("step.observe", "automation", **{"row 10482": "x"})
    assert "10482" not in writer.events_path.read_text()
