"""`eagleview replay` end to end: result JSON on stdout, the summary on stderr, exit codes."""

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from eagleview.artifacts.yaml_io import dump_model
from eagleview.cli import app
from tests.fixtures import CAPABILITY
from tests.support.bank import Bank
from tests.support.replay import policy_for, tenant_for


def flat(text: str) -> str:
    """Rich boxes, wraps and (on CI) colours usage errors; compare words only."""
    plain = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return " ".join(plain.replace("│", " ").split())


@pytest.fixture
def cli_args(lantern: Bank, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    tenant = tmp_path / "tenant.yaml"
    tenant.write_text(dump_model(tenant_for(lantern)), encoding="utf-8")
    policy = tmp_path / "policy.yaml"
    policy.write_text(dump_model(policy_for(lantern)), encoding="utf-8")
    monkeypatch.setenv(lantern.tenant.password_env, lantern.password)
    return [
        "--tenant-file",
        str(tenant),
        "--policy",
        str(policy),
        "--runs-dir",
        str(tmp_path / "runs"),
    ]


def test_success_prints_the_result_and_exits_0(cli_args: list[str], tmp_path: Path) -> None:
    out = CliRunner().invoke(app, ["replay", str(CAPABILITY), "-i", "member_id= 10482", *cli_args])
    assert out.exit_code == 0, out.stderr
    result = json.loads(out.stdout)
    assert result["status"] == "success"
    assert result["outputs"]["share_accounts"][0]["balance"].count(".") == 1  # a string, not float
    assert "success in" in out.stderr
    assert (tmp_path / "runs" / result["run_id"] / "summary.json").is_file()


def test_outcome_exits_3(cli_args: list[str]) -> None:
    out = CliRunner().invoke(app, ["replay", str(CAPABILITY), "-i", "member_id=99999", *cli_args])
    assert out.exit_code == 3
    assert json.loads(out.stdout)["outcome"]["code"] == "MEMBER_NOT_FOUND"
    assert "outcome MEMBER_NOT_FOUND at submit_search" in out.stderr


def test_rejected_exits_4(cli_args: list[str]) -> None:
    out = CliRunner().invoke(app, ["replay", str(CAPABILITY), "-i", "member_id=10 482", *cli_args])
    assert out.exit_code == 4
    assert json.loads(out.stdout)["rejection"]["code"] == "INPUT_INVALID"


def test_unattended_with_approve_gates_is_a_usage_error() -> None:
    out = CliRunner().invoke(
        app, ["replay", str(CAPABILITY), "-i", "member_id=1", "--unattended", "--approve-gates"]
    )
    assert out.exit_code == 2
    assert "never approves its own irreversible steps" in flat(out.output)


def test_unattended_with_allow_modified_is_a_usage_error() -> None:
    out = CliRunner().invoke(app, ["replay", str(CAPABILITY), "--unattended", "--allow-modified"])
    assert out.exit_code == 2


def test_edited_artifact_names_the_file_and_the_way_out(tmp_path: Path) -> None:
    edited = tmp_path / "edited.yaml"
    edited.write_text(CAPABILITY.read_text().replace("Read-only.", "Read only."), encoding="utf-8")
    out = CliRunner().invoke(app, ["replay", str(edited), "-i", "member_id=10482"])
    assert out.exit_code == 2
    assert str(edited) in out.stderr and "--allow-modified" in out.stderr


def test_help_documents_input_normalization() -> None:
    out = CliRunner().invoke(app, ["replay", "--help"])
    assert "trimmed of surrounding whitespace" in out.stdout
    assert "INPUT_INVALID" in out.stdout
