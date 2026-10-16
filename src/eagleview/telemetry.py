"""Drift telemetry: which locator won, at which rank, across many runs.

A capability's locators are ranked when it is compiled; rank 0 is the most semantic one that
resolved uniquely. When rank 0 keeps winning, the screen is as recorded. When a later rank starts
winning, or the top two stop agreeing, the screen has drifted: a label was renamed or a layout
moved. That shows up here before any run fails."""

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class StepStats:
    runs: int = 0
    winners: Counter[str] = field(default_factory=Counter[str])  # "strategy@rank"
    agreed: int = 0
    compared: int = 0

    @property
    def rank0_share(self) -> float:
        top = sum(n for k, n in self.winners.items() if k.endswith("@0"))
        return top / self.runs if self.runs else 0.0

    @property
    def agreement(self) -> float | None:
        return self.agreed / self.compared if self.compared else None


Key = tuple[str, str, str]  # capability, tenant, step


def collect(runs_root: Path) -> dict[Key, StepStats]:
    stats: dict[Key, StepStats] = defaultdict(StepStats)
    for summary in sorted(runs_root.glob("*/summary.json")):
        data: dict[str, Any] = json.loads(summary.read_text())
        for entry in data.get("locator_report") or []:
            s = stats[(data.get("capability", "?"), data.get("tenant", "?"), entry["step_id"])]
            s.runs += 1
            s.winners[f"{entry['strategy']}@{entry['rank']}"] += 1
            if entry.get("agreement") is not None:
                s.compared += 1
                s.agreed += bool(entry["agreement"])
    return dict(stats)


def report(stats: dict[Key, StepStats]) -> str:
    lines = ["capability | tenant | step | runs | winners | rank-0 share | agreement"]
    for (cap, tenant, step), s in sorted(stats.items()):
        winners = ", ".join(f"{k}x{n}" for k, n in s.winners.most_common())
        agreement = "n/a" if s.agreement is None else f"{s.agreement:.0%}"
        flag = "" if s.rank0_share == 1.0 else "  <- drift"
        lines.append(
            f"{cap} | {tenant} | {step} | {s.runs} | {winners} | {s.rank0_share:.0%} | "
            f"{agreement}{flag}"
        )
    return "\n".join(lines)
