"""Nothing personal in /evidence, and a self-test that the scan can fail."""

import json
from pathlib import Path

import pytest

from mock_bank.config import all_tenants
from mock_bank.seed import generate
from tests.evidence.pii_scan import REPO, scan

EVIDENCE = REPO / "evidence"


@pytest.mark.skipif(not EVIDENCE.is_dir(), reason="no evidence generated yet")
def test_the_committed_evidence_holds_no_personal_data() -> None:
    hits = scan(EVIDENCE)
    assert not hits, "\n".join(
        f"{h.path.relative_to(REPO)}: {h.what} {h.sample}" for h in hits[:20]
    )


def test_the_scan_finds_a_planted_name_number_and_pattern(tmp_path: Path) -> None:
    tenant = next(t for t in all_tenants() if t.slug == "lantern-bay")
    member = generate(tenant)["10311"]
    (tmp_path / "summary.json").write_text(json.dumps({"note": f"seen {member.display_name}"}))
    (tmp_path / "run.jsonl").write_text(json.dumps({"timing_ms": 10234, "member": "10234"}) + "\n")
    (tmp_path / "index.html").write_text("<td>SSN: 123-45-6789</td>")
    (tmp_path / "README.md").write_text("The demo member is 10482.")  # allowed here only
    whats = sorted({h.what for h in scan(tmp_path)})
    assert whats == ["member number", "name", "ssn"]


def test_numbers_that_are_not_strings_are_not_member_numbers(tmp_path: Path) -> None:
    (tmp_path / "summary.json").write_text(json.dumps({"timing_ms": {"total": 10482}}))
    assert scan(tmp_path) == []


def test_a_member_number_inside_a_hash_is_not_a_member_number(tmp_path: Path) -> None:
    (tmp_path / "artifact.yaml").write_text("content_hash: sha256:3f10446dff2ffe1e2110b92d")
    assert scan(tmp_path) == []


def test_a_scenario_record_is_written_redacted(tmp_path: Path) -> None:
    """result.json goes through the evidence redactor too: scenario 14's record, from a fake
    answer, plus a member number that slipped into a message, scans clean."""
    from scripts.evidence.driver import write_record

    record = {
        "scenario": "14",
        "name": "agent_catalog",
        "status": "pass",
        "asked": [
            {
                "question": 1,
                "agent_run": "r_20261016T060000_abcdef",
                "tool_calls": [
                    {"tool": "member_lookup_share_balances", "status": "success", "run_id": "r_1"}
                ],
            }
        ],
        "mismatch": "expected member 10482 to have 3 shares",
    }
    write_record(tmp_path, record)
    assert scan(tmp_path) == []
    assert "10482" not in (tmp_path / "result.json").read_text()
