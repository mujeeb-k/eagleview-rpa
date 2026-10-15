"""Seed data: deterministic, well-formed, and pinned where the demo narrative depends on it."""

from decimal import Decimal

import pytest

from mock_bank.config import all_tenants
from mock_bank.seed import (
    DEMO_MEMBER,
    INJECTION_NOTE,
    MEMBER_COUNT,
    SHARE_TYPES,
    Member,
    generate,
    seed_members,
)


@pytest.fixture(scope="module")
def members() -> dict[str, Member]:
    return generate(7)


def test_same_seed_same_members(members: dict[str, Member]) -> None:
    assert generate(7) == members
    assert generate(8) != members


def test_member_numbers(members: dict[str, Member]) -> None:
    assert len(members) == MEMBER_COUNT
    assert list(members) == [str(n) for n in range(10001, 10001 + MEMBER_COUNT)]


def test_golden_demo_member(members: dict[str, Member]) -> None:
    # Pinned so a Faker upgrade that changes the data fails loudly instead of silently drifting
    # every committed artifact and evidence run. Regenerate deliberately if this ever changes.
    m = members[DEMO_MEMBER]
    assert m.display_name == "MEJIA, AMBER K"
    assert m.masked_ssn == "***-**-0414"
    assert [(s.suffix, s.balance, s.available) for s in m.shares] == [
        ("S00", Decimal("3019.36"), Decimal("3019.36")),
        ("S01", Decimal("1661.07"), Decimal("1384.43")),
        ("S10", Decimal("74.86"), Decimal("74.86")),
    ]


def test_money_is_two_place_decimal(members: dict[str, Member]) -> None:
    for m in members.values():
        amounts = [s.balance for s in m.shares] + [s.available for s in m.shares]
        amounts += [ln.balance for ln in m.loans] + [x.amount for x in m.txns]
        for a in amounts:
            assert isinstance(a, Decimal)
            assert a.as_tuple().exponent == -2


def test_share_shape(members: dict[str, Member]) -> None:
    for m in members.values():
        suffixes = [s.suffix for s in m.shares]
        assert suffixes[0] == "S00"
        assert set(suffixes) <= set(SHARE_TYPES)
        for s in m.shares:
            assert Decimal("0.00") <= s.available <= s.balance
    # The draws actually vary: some members have every optional share, some have none.
    counts = {len(m.shares) for m in members.values()}
    assert 1 in counts and 4 in counts


def test_history_uses_ach_and_pos(members: dict[str, Member]) -> None:
    descriptions = {x.description.split(" ")[0] for m in members.values() for x in m.txns}
    assert descriptions == {"ACH", "POS"}


def test_both_tenants_load_the_same_dataset() -> None:
    # Scenario 13 replays one input on both tenants, so member 10482 must match across them.
    tenants = all_tenants()
    assert {t.seed for t in tenants} == {7}
    datasets = [seed_members(t.seed, t.injection_member) for t in tenants]
    assert datasets[0][DEMO_MEMBER] == datasets[1][DEMO_MEMBER]


def test_exactly_one_member_carries_the_injection_note() -> None:
    members = seed_members(7, "10377")
    assert [n for n, m in members.items() if m.notes == INJECTION_NOTE] == ["10377"]
