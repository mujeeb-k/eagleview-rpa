"""Seed data: deterministic, separate per tenant, and pinned where the demo depends on it."""

import json
import subprocess
import sys
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

import pytest

import mock_bank.seed
from mock_bank.config import Tenant, load_tenant
from mock_bank.seed import INJECTION_NOTE, MEMBER_COUNT, Member, generate

LANTERN = load_tenant("lantern-bay")
COPPERHILL = load_tenant("copperhill")
TENANTS = [LANTERN, COPPERHILL]

# Pinned so a Faker upgrade or generator change that shifts the data fails loudly instead of
# silently drifting every committed artifact and evidence run. Regenerate deliberately if it must.
GOLDEN: dict[str, tuple[str, str, list[tuple[str, str, str, str]]]] = {
    "lantern-bay": (
        "MEJIA, AMBER K",
        "***-**-0414",
        [
            ("S00", "Regular Share", "3019.36", "3019.36"),
            ("S01", "Share Draft", "1661.07", "1384.43"),
            ("S10", "Holiday Club", "74.86", "74.86"),
        ],
    ),
    "copperhill": (
        "BROWN, AMY R",
        "***-**-2398",
        [
            ("01", "Primary Savings", "1327.31", "1327.31"),
            ("05", "Share Checking", "713.55", "496.71"),
            ("20", "Christmas Club", "97.67", "97.67"),
        ],
    ),
}


@pytest.fixture(scope="module", params=TENANTS, ids=lambda t: t.slug)
def tenant(request: pytest.FixtureRequest) -> Tenant:
    return request.param


@pytest.fixture(scope="module")
def members(tenant: Tenant) -> dict[str, Member]:
    return generate(tenant)


def test_same_config_same_members(tenant: Tenant, members: dict[str, Member]) -> None:
    assert generate(tenant) == members


def test_member_numbers(tenant: Tenant, members: dict[str, Member]) -> None:
    assert len(members) == MEMBER_COUNT
    first = tenant.first_member
    assert list(members) == [str(n) for n in range(first, first + MEMBER_COUNT)]
    digits = {"lantern-bay": 5, "copperhill": 7}[tenant.slug]
    assert {len(n) for n in members} == {digits}
    assert tenant.demo_member in members and tenant.injection_member in members


def test_golden_demo_member(tenant: Tenant, members: dict[str, Member]) -> None:
    name, ssn, shares = GOLDEN[tenant.slug]
    m = members[tenant.demo_member]
    assert m.display_name == name
    assert m.masked_ssn == ssn
    assert [(s.suffix, s.description, s.balance, s.available) for s in m.shares] == [
        (sfx, desc, Decimal(bal), Decimal(avail)) for sfx, desc, bal, avail in shares
    ]


def test_money_is_two_place_decimal(members: dict[str, Member]) -> None:
    for m in members.values():
        amounts = [s.balance for s in m.shares] + [s.available for s in m.shares]
        amounts += [ln.balance for ln in m.loans] + [x.amount for x in m.txns]
        for a in amounts:
            assert isinstance(a, Decimal)
            assert a.as_tuple().exponent == -2


def test_shares_use_the_tenants_own_types(tenant: Tenant, members: dict[str, Member]) -> None:
    labels = {
        kind: (tenant.suffix(st.low), st.description) for kind, st in tenant.share_types.items()
    }
    e = tenant.edge_cases
    for m in members.values():
        if m.number != e.loans_only_member:
            assert m.shares[0].kind == "base"
        for s in m.shares:
            assert (s.suffix, s.description) == labels[s.kind]
            if m.number != e.overdrawn_member:
                assert Decimal("0.00") <= s.available <= s.balance
    # The draws actually vary: some members have every share kind, some only the base share.
    counts = {len(m.shares) for m in members.values()}
    assert 1 in counts and 4 in counts


def test_history_uses_ach_and_pos(members: dict[str, Member]) -> None:
    descriptions = {x.description.split(" ")[0] for m in members.values() for x in m.txns}
    assert descriptions == {"ACH", "POS"}


def test_exactly_one_member_carries_the_injection_note(
    tenant: Tenant, members: dict[str, Member]
) -> None:
    carriers = [n for n, m in members.items() if m.notes == INJECTION_NOTE]
    assert carriers == [tenant.injection_member]


def test_tenants_share_no_members_or_people() -> None:
    a, b = generate(LANTERN), generate(COPPERHILL)
    assert not set(a) & set(b)
    assert not {m.ssn for m in a.values()} & {m.ssn for m in b.values()}
    assert LANTERN.seed != COPPERHILL.seed


def test_share_codes_and_labels_differ_between_tenants() -> None:
    def labels(t: Tenant) -> set[tuple[str, str]]:
        return {(t.suffix(st.low), st.description) for st in t.share_types.values()}

    assert not labels(LANTERN) & labels(COPPERHILL)
    assert not set(LANTERN.products) & set(COPPERHILL.products)


def _digest(members: dict[str, Member]) -> str:
    return json.dumps({n: asdict(m) for n, m in members.items()}, default=str, sort_keys=True)


# Runs the generator in a fresh interpreter whose clock says 2031-02-03: date.today(),
# datetime.now() and friends are replaced before Faker or the seed module are imported.
_FAKE_CLOCK = """
import datetime as _dt, json, sys
from dataclasses import asdict

class _Date(_dt.date):
    @classmethod
    def today(cls): return cls(2031, 2, 3)

class _DateTime(_dt.datetime):
    @classmethod
    def now(cls, tz=None): return cls(2031, 2, 3, 9, 30, tzinfo=tz)
    @classmethod
    def today(cls): return cls(2031, 2, 3, 9, 30)
    @classmethod
    def utcnow(cls): return cls(2031, 2, 3, 9, 30)

_dt.date, _dt.datetime = _Date, _DateTime
from mock_bank.config import load_tenant
from mock_bank.seed import generate
members = generate(load_tenant(sys.argv[1]))
assert _dt.date.today().year == 2031
print(json.dumps({n: asdict(m) for n, m in members.items()}, default=str, sort_keys=True))
"""


def test_seeded_data_does_not_depend_on_todays_date(
    tenant: Tenant, members: dict[str, Member]
) -> None:
    """Everything is anchored to the frozen business date (2026-10-15), so data and evidence are
    the same whatever day they are generated on."""
    out = subprocess.run(
        [sys.executable, "-c", _FAKE_CLOCK, tenant.slug],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parents[2],
    ).stdout
    assert out.strip() == _digest(members)


def test_edge_cases(tenant: Tenant, members: dict[str, Member]) -> None:
    e = tenant.edge_cases
    overdrawn = members[e.overdrawn_member]
    draft = next(s for s in overdrawn.shares if s.kind == "draft")
    assert draft.balance == draft.available == Decimal(e.overdrawn_balance) < 0

    loans_only = members[e.loans_only_member]
    assert loans_only.shares == [] and loans_only.loans
    assert {t.suffix for t in loans_only.txns} <= {ln.suffix for ln in loans_only.loans}

    hyphenated = members[e.hyphenated_member]
    assert "'" in hyphenated.last and "-" in hyphenated.last
    assert hyphenated.display_name.startswith(e.hyphenated_last.upper() + ", ")
    assert "'" not in hyphenated.email


def test_edge_cases_change_only_their_own_members(
    tenant: Tenant, members: dict[str, Member], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Applied after the draws, with per-member randomness: every other member, the pinned demo
    member included, is exactly what it was before edge cases existed."""
    monkeypatch.setattr(mock_bank.seed, "_apply_edge_cases", lambda *_: None)
    plain = generate(tenant)
    e = tenant.edge_cases
    edge = {e.overdrawn_member, e.loans_only_member, e.hyphenated_member}
    for number, member in members.items():
        assert (member == plain[number]) is (number not in edge), number
