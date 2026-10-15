"""Seed data: deterministic, separate per tenant, and pinned where the demo depends on it."""

from decimal import Decimal

import pytest

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
    for m in members.values():
        assert m.shares[0].kind == "base"
        for s in m.shares:
            assert (s.suffix, s.description) == labels[s.kind]
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
