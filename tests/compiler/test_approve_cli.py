"""`eagleview approve` is a person's step: only verified drafts, never over an existing file."""

from pathlib import Path

from typer.testing import CliRunner

from eagleview.artifacts.loader import load_for_run
from eagleview.artifacts.yaml_io import dump_capability, load_capability
from eagleview.cli import app
from tests.fixtures import CAPABILITY, STALE_EXPECT


def test_approve_promotes_a_verified_draft_and_records_the_reviewer(tmp_path: Path) -> None:
    draft = tmp_path / "drafts" / "member.lookup_share_balances.yaml"
    dump_capability(load_for_run(CAPABILITY).capability, draft)
    into = tmp_path / "capabilities"
    out = CliRunner().invoke(
        app, ["approve", str(draft), "--reviewer", "dana", "--into", str(into)]
    )
    assert out.exit_code == 0, out.output
    approved = load_capability(into / "member.lookup_share_balances.yaml")
    assert approved.status == "approved"
    assert approved.provenance.approved_by is not None
    assert approved.provenance.approved_by.reviewer == "dana"
    again = CliRunner().invoke(
        app, ["approve", str(draft), "--reviewer", "dana", "--into", str(into)]
    )
    assert again.exit_code == 2 and "exists" in again.stderr


def test_approve_refuses_a_draft_that_was_never_verified(tmp_path: Path) -> None:
    out = CliRunner().invoke(
        app, ["approve", str(STALE_EXPECT), "--reviewer", "dana", "--into", str(tmp_path)]
    )
    assert out.exit_code == 2 and "only a verified capability" in out.stderr
