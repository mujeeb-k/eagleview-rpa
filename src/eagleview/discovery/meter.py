"""What discovery spends, and the limit on it.

Every real API response is priced from its usage and appended to a ledger the moment it arrives,
so a crash still records the spend. Before every real request the ledger is checked against the
limits: at most $25 in total and at most 33 runs, where a run counts once it has made its first
real call. Tests inject a meter that never touches the real ledger."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

MTOK = Decimal(1_000_000)


@dataclass(frozen=True)
class Price:
    """Dollars per million tokens. Cache writes are the 5-minute kind (1.25x input)."""

    input: Decimal
    cache_write: Decimal
    cache_read: Decimal
    output: Decimal


# First-party Claude API rates (claude-api skill, cached 2026-10-06). Thinking bills as output.
PRICES: dict[str, Price] = {
    "claude-sonnet-5-5": Price(Decimal("2"), Decimal("2.5"), Decimal("0.2"), Decimal("10")),
    "claude-opus-5-5": Price(Decimal("4"), Decimal("5"), Decimal("0.2"), Decimal("20")),
}


@dataclass(frozen=True)
class Limits:
    max_usd: Decimal = Decimal("25")
    max_runs: int = 45  # 30, then 33, raised by the user's authorisation (docs/DECISIONS.md)


class BudgetExceeded(Exception):
    pass


def tokens(usage: Any) -> dict[str, int]:
    return {
        "input": int(getattr(usage, "input_tokens", 0) or 0),
        "cache_write": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        "cache_read": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        "output": int(getattr(usage, "output_tokens", 0) or 0),
    }


def price_of(models: list[str], counts: Mapping[str, int]) -> Decimal:
    """Priced at the dearest model that took part (a fallback can serve part of a turn)."""
    known = [PRICES[m] for m in models if m in PRICES]
    if not known:
        raise BudgetExceeded(f"no price for {models}: refusing to spend blind")
    p = max(known, key=lambda x: x.output)
    total = (
        counts["input"] * p.input
        + counts["cache_write"] * p.cache_write
        + counts["cache_read"] * p.cache_read
        + counts["output"] * p.output
    )
    return (total / MTOK).quantize(Decimal("0.000001"))


class Meter(Protocol):
    def before_request(self, run_id: str) -> None: ...

    def after_response(
        self, run_id: str, requested: str, response: Any, purpose: str
    ) -> Decimal: ...


@dataclass
class Spend:
    usd: Decimal
    runs: int


class Ledger:
    """`runs/discovery_ledger.jsonl`: one line per real API response. Gitignored, never redacted
    of anything: it holds token counts and dollars only."""

    def __init__(self, path: Path, limits: Limits | None = None) -> None:
        self.path = path
        self.limits = limits or Limits()

    def _lines(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def spend(self) -> Spend:
        lines = self._lines()
        usd = sum((Decimal(line["usd"]) for line in lines), Decimal(0))
        return Spend(usd, len({line["run_id"] for line in lines}))

    def run_spend(self, run_id: str) -> Decimal:
        return sum((Decimal(x["usd"]) for x in self._lines() if x["run_id"] == run_id), Decimal(0))

    def check(self, run_id: str) -> None:
        lines = self._lines()
        spend = self.spend()
        if spend.usd >= self.limits.max_usd:
            raise BudgetExceeded(
                f"discovery spend is ${spend.usd:.2f}, at the ${self.limits.max_usd} limit"
            )
        counted = any(line["run_id"] == run_id for line in lines)
        if not counted and spend.runs >= self.limits.max_runs:
            raise BudgetExceeded(f"{spend.runs} real discovery runs, at the limit")

    def record(self, entry: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")


class LedgerMeter:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    def before_request(self, run_id: str) -> None:
        self.ledger.check(run_id)

    def after_response(self, run_id: str, requested: str, response: Any, purpose: str) -> Decimal:
        served = str(getattr(response, "model", requested) or requested)
        counts = tokens(getattr(response, "usage", None))
        usd = price_of([requested, served], counts)
        self.ledger.record(
            {
                "ts": datetime.now(UTC).isoformat(),
                "run_id": run_id,
                "purpose": purpose,
                "requested_model": requested,
                "served_model": served,
                "tokens": counts,
                "usd": str(usd),
            }
        )
        return usd


class MemoryMeter:
    """For tests and fake transports: prices like the real meter, records in memory only."""

    def __init__(self, limits: Limits | None = None) -> None:
        self.limits = limits or Limits()
        self.entries: list[tuple[str, Decimal]] = []

    def before_request(self, run_id: str) -> None:
        if sum((u for _, u in self.entries), Decimal(0)) >= self.limits.max_usd:
            raise BudgetExceeded("memory meter limit")

    def after_response(self, run_id: str, requested: str, response: Any, purpose: str) -> Decimal:
        served = str(getattr(response, "model", requested) or requested)
        usd = price_of([requested, served], tokens(getattr(response, "usage", None)))
        self.entries.append((purpose, usd))
        return usd
