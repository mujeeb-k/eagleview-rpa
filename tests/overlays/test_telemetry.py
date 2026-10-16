"""Drift telemetry reads run summaries and flags steps whose rank-0 locator stopped winning."""

import json
from pathlib import Path

from typer.testing import CliRunner

from eagleview.cli import app
from eagleview.telemetry import collect


def summary(root: Path, run: str, tenant: str, strategy: str, rank: int, agreed: bool) -> None:
    (root / run).mkdir(parents=True)
    (root / run / "summary.json").write_text(
        json.dumps(
            {
                "capability": "member.lookup_share_balances@1.0.0",
                "tenant": tenant,
                "locator_report": [
                    {
                        "step_id": "submit_search",
                        "strategy": strategy,
                        "rank": rank,
                        "agreement": agreed,
                    }
                ],
            }
        )
    )


def test_a_later_rank_winning_is_flagged_as_drift(tmp_path: Path) -> None:
    summary(tmp_path, "r_1", "lantern-bay", "anchor", 0, True)
    summary(tmp_path, "r_2", "lantern-bay", "anchor", 0, True)
    summary(tmp_path, "r_3", "lantern-bay", "css", 1, False)
    stats = collect(tmp_path)
    [step] = stats.values()
    assert step.runs == 3 and round(step.rank0_share, 2) == 0.67
    assert step.agreement is not None and round(step.agreement, 2) == 0.67
    out = CliRunner().invoke(app, ["telemetry", str(tmp_path)])
    assert out.exit_code == 0 and "<- drift" in out.stdout
    as_json = json.loads(CliRunner().invoke(app, ["telemetry", str(tmp_path), "--json"]).stdout)
    assert as_json[0]["winners"] == {"anchor@0": 2, "css@1": 1}
