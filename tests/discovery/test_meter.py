"""The spend ledger: priced from usage, written per response, and enforcing both limits."""

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from eagleview.discovery.meter import BudgetExceeded, Ledger, LedgerMeter, Limits, price_of


def response(model: str, **usage: int) -> SimpleNamespace:
    return SimpleNamespace(model=model, usage=SimpleNamespace(**usage))


def test_prices_follow_the_rate_card() -> None:
    counts = {
        "input": 1_000_000,
        "cache_write": 1_000_000,
        "cache_read": 1_000_000,
        "output": 1_000_000,
    }
    assert price_of(["claude-sonnet-5-5"], counts) == Decimal("14.700000")
    assert price_of(["claude-opus-5-5"], counts) == Decimal("29.200000")
    # A turn a fallback served is priced at the dearer model.
    assert price_of(["claude-sonnet-5-5", "claude-opus-5-5"], counts) == Decimal("29.200000")
    with pytest.raises(BudgetExceeded, match="no price"):
        price_of(["some-unknown-model"], counts)


def test_each_response_is_ledgered_and_the_dollar_limit_stops_the_next(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl", Limits(max_usd=Decimal("0.05"), max_runs=30))
    meter = LedgerMeter(ledger)
    meter.before_request("r_a")
    usd = meter.after_response(
        "r_a",
        "claude-sonnet-5-5",
        response("claude-sonnet-5-5", input_tokens=10_000, output_tokens=3_000),
        "turn",
    )
    assert usd == Decimal("0.050000")
    assert len((tmp_path / "ledger.jsonl").read_text().splitlines()) == 1
    with pytest.raises(BudgetExceeded, match="limit"):
        meter.before_request("r_a")


def test_a_run_counts_once_it_has_spent_and_the_run_limit_holds(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl", Limits(max_usd=Decimal("25"), max_runs=2))
    meter = LedgerMeter(ledger)
    for run in ("r_1", "r_2"):
        meter.before_request(run)
        meter.after_response(
            run, "claude-sonnet-5-5", response("claude-sonnet-5-5", input_tokens=10), "turn"
        )
    meter.before_request("r_2")  # a counted run may continue
    with pytest.raises(BudgetExceeded, match="2 real discovery runs"):
        meter.before_request("r_3")
    assert ledger.spend().runs == 2
