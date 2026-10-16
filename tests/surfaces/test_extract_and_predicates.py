"""Predicates and the extract engine on live screens: scalar reads through anchors, the anchored
table extractor with money_us -> Decimal, and every leaf predicate kind."""

from decimal import Decimal
from typing import Any

import pytest

from eagleview.schema.capability import ScalarExtract, TableSpec
from eagleview.surfaces.base import PredicateContext
from eagleview.surfaces.extract import ExtractError, parse_rows
from eagleview.surfaces.web.playwright_surface import PlaywrightSurface
from mock_bank.seed import generate
from tests.support.bank import Bank
from tests.surfaces.helpers import (
    MEMBER_FIELD,
    act_on,
    pred,
    spec,
    to_inquiry,
    to_member,
    wait_until,
)

SHARES = TableSpec.model_validate(
    {
        "anchor_text": "Share Accounts",
        "frame": ["main"],
        "columns": {
            "suffix": "Sfx",
            "description": "Description",
            "balance": "Balance",
            "available": "Available",
        },
        "parse": {"balance": "money_us", "available": "money_us"},
    }
)
NAME = ScalarExtract.model_validate(
    {
        "target": {
            "frame": ["main"],
            "locators": [
                {
                    "strategy": "anchor",
                    "anchor_text": "Name:",
                    "relation": "right_of",
                    "control": "text",
                }
            ],
        }
    }
)

ALIASES = {"Member #": ["Acct Holder No."], "Member Inquiry": ["Member Lookup"]}


async def test_reads_name_and_share_balances_on_both_tenants(open_surface: Any, bank: Bank) -> None:
    surface: PlaywrightSurface = await open_surface(bank, label_aliases=ALIASES)
    await to_member(surface, bank, bank.demo)
    member = generate(bank.tenant)[bank.demo]

    assert await surface.extract(NAME) == member.display_name
    raw = await surface.extract(SHARES)
    assert isinstance(raw, list)
    rows = parse_rows(raw, SHARES.parse)
    assert rows == [
        {
            "suffix": s.suffix,
            "description": s.description,
            "balance": s.balance,
            "available": s.available,
        }
        for s in member.shares
    ]
    assert all(isinstance(r["balance"], Decimal) for r in rows)


async def test_reads_a_table_whose_rows_run_below_the_fold(
    open_surface: Any, lantern: Bank
) -> None:
    """Recent Activity on a tall member page: rows are scrolled into view one by one and hit-tested,
    then the frame's scroll position is put back. Debits use the trailing-minus ledger format."""
    surface: PlaywrightSurface = await open_surface(lantern)
    await to_member(surface, lantern, "10014")
    main = surface.page.frame(name="main")
    assert main is not None
    activity = TableSpec.model_validate(
        {
            "anchor_text": "Recent Activity",
            "frame": ["main"],
            "columns": {"suffix": "Sfx", "amount": "Amount"},
            "parse": {"amount": "money_us"},
        }
    )
    raw = await surface.extract(activity)
    assert isinstance(raw, list)
    rows = parse_rows(raw, activity.parse)
    member = generate(lantern.tenant)["10014"]
    assert [r["amount"] for r in rows] == [t.amount for t in member.txns[:10]]
    assert any(isinstance(r["amount"], Decimal) and r["amount"] < 0 for r in rows)
    assert await main.evaluate("() => window.scrollY") == 0


async def test_extract_refuses_ambiguity_and_absence(open_surface: Any, lantern: Bank) -> None:
    surface: PlaywrightSurface = await open_surface(lantern)
    await to_member(surface, lantern, lantern.demo)
    missing = SHARES.model_copy(update={"anchor_text": "Certificate Accounts"})
    with pytest.raises(ExtractError) as info:
        await surface.extract(missing)
    assert info.value.code == "not_found"
    wrong_column = TableSpec.model_validate(
        {"anchor_text": "Share Accounts", "frame": ["main"], "columns": {"rate": "Rate"}}
    )
    with pytest.raises(ExtractError) as info:
        await surface.extract(wrong_column)
    assert info.value.code == "column_missing"
    description = ScalarExtract.model_validate(
        {"target": {"frame": ["main"], "locators": [{"strategy": "text", "text": "Description"}]}}
    )
    with pytest.raises(ExtractError) as info:
        await surface.extract(description)
    assert info.value.code == "ambiguous"


async def test_leaf_predicates(open_surface: Any, lantern: Bank) -> None:
    surface: PlaywrightSurface = await open_surface(lantern)
    await to_inquiry(surface)
    assert (await act_on(surface, MEMBER_FIELD, "fill", text="99999")).ok
    go = spec({"strategy": "css", "selector": "img[src*='go']"}, frame=["main"])
    assert (await act_on(surface, go)).ok

    async def holds(p: dict[str, Any], ctx: PredicateContext | None = None) -> bool:
        return await surface.check(pred(p), ctx)

    not_found = {"text_matches": {"frame": "main", "pattern": "(?i)no member(s)? found"}}
    await wait_until(surface, not_found)
    # A phrase split across inline elements matches as a line.
    assert await holds({"text_present": {"frame": "main", "text": "Member Search"}})
    assert not await holds({"text_present": {"frame": "nav", "text": "Member Search"}})
    assert await holds({"text_present": {"text": "Member Search"}})  # any frame
    assert await holds({"url_matches": {"pattern": r"/inq$"}})
    assert await holds({"title_matches": {"pattern": "LANTERN BAY"}})
    assert await holds({"element_present": {"target": MEMBER_FIELD.model_dump(mode="json")}})
    # The re-rendered form keeps what was typed; field_value reads it.
    assert await holds(
        {"field_value": {"target": "self", "equals": "99999"}},
        PredicateContext(self_target=MEMBER_FIELD),
    )
    assert not await holds({"field_value": {"target": "self", "equals": "99999"}})  # no self
    assert await holds(
        {"output_present": "share_accounts"},
        PredicateContext(outputs={"share_accounts": [{"x": 1}]}),
    )
    assert not await holds(
        {"output_present": "share_accounts"}, PredicateContext(outputs={"share_accounts": []})
    )
    combined = {
        "all": [
            not_found,
            {"not": {"text_present": {"frame": "main", "text": "Share Accounts"}}},
            {
                "any": [
                    {"url_matches": {"pattern": "nope"}},
                    {"title_matches": {"pattern": "Teller"}},
                ]
            },
        ]
    }
    assert await holds(combined)
