"""Parsers and column mapping: pure, no browser. Money never passes through float."""

from decimal import Decimal

import pytest

from eagleview.surfaces.extract import ExtractError, Grid, integer, map_columns, money_us, parse


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$1,234.56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("$0.00", "0.00"),
        ("12.34", "12.34"),
        ("$1,000,000.01", "1000000.01"),
        ("  $ 12.34 ", "12.34"),
        ("$1 234.56".replace(" ", ""), "1234.56"),
        # Leading minus, as the mock bank prints a negative balance.
        ("-$1,234.56", "-1234.56"),
        ("-12.34", "-12.34"),
        # Trailing ledger minus, as the mock bank prints a debit in Recent Activity.
        ("12.34-", "-12.34"),
        ("$1,234.56-", "-1234.56"),
        ("1,234.56 -", "-1234.56"),
        # Accounting parentheses.
        ("(12.34)", "-12.34"),
        ("($1,234.56)", "-1234.56"),
        ("( 12.34 )", "-12.34"),
        # DR (debit) is negative, CR (credit) is positive; any case, with or without a space.
        ("12.34 DR", "-12.34"),
        ("12.34DR", "-12.34"),
        ("$1,234.56 dr", "-1234.56"),
        ("12.34 CR", "12.34"),
        ("$1,234.56CR", "1234.56"),
    ],
)
def test_money_us_formats(text: str, expected: str) -> None:
    value = money_us(text)
    assert isinstance(value, Decimal)
    assert value == Decimal(expected)
    assert str(value) == expected  # exact scale preserved: "0.00", not "0"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "12",  # no cents
        "12.3",  # one cent digit
        "12.345",
        "1,23.45",  # malformed thousands
        "12,34.56",
        "$",
        "abc",
        "(12.34",  # unbalanced
        "12.34)",
        "-12.34-",  # two negative markers
        "(12.34)-",
        "-12.34 DR",
        "(12.34) DR",
        "12.34- DR",
        "-12.34 CR",  # credit that is also negative: contradictory
        "(12.34) CR",
        "12.34 XX",
        "1e3",
        "12.34 DR CR",
    ],
)
def test_money_us_rejects(text: str) -> None:
    with pytest.raises(ExtractError) as info:
        money_us(text)
    assert info.value.code == "parse"


def test_integer_and_text() -> None:
    assert integer("1,234") == 1234
    assert integer("-42") == -42
    assert integer("10482") == 10482
    with pytest.raises(ExtractError):
        integer("12.5")
    with pytest.raises(ExtractError):
        integer("1,23")
    assert parse("  Regular  Share ", "text") == "Regular Share"
    assert parse("$5.00", "money_us") == Decimal("5.00")


GRID = Grid(
    header=["Sfx", "Description", "Nickname", "Balance", "Available"],
    rows=[
        ["S00", "Regular Share", "", "$25.00", "$20.00"],
        ["S10", "Holiday Club", "Gifts", "$1,204.17", "$1,204.17"],
    ],
)
COLUMNS = {"suffix": "Sfx", "description": "Description", "balance": "Balance"}


def test_map_columns_by_header_label() -> None:
    rows = map_columns(GRID, COLUMNS)
    assert rows == [
        {"suffix": "S00", "description": "Regular Share", "balance": "$25.00"},
        {"suffix": "S10", "description": "Holiday Club", "balance": "$1,204.17"},
    ]


def test_map_columns_case_colon_and_alias() -> None:
    grid = Grid(header=["SFX:", "Desc", "Bal"], rows=[["01", "Primary Savings", "$1.00"]])
    rows = map_columns(
        grid,
        COLUMNS,
        {"Description": ["Desc"], "Balance": ["Bal"]},
    )
    assert rows == [{"suffix": "01", "description": "Primary Savings", "balance": "$1.00"}]


def test_map_columns_skips_message_rows_and_refuses_ragged_ones() -> None:
    grid = Grid(header=["Sfx", "Balance"], rows=[["No loan accounts."]])
    assert map_columns(grid, {"suffix": "Sfx", "balance": "Balance"}) == []
    ragged = Grid(header=["Sfx", "Description", "Balance"], rows=[["S00", "$1.00"]])
    with pytest.raises(ExtractError) as info:
        map_columns(ragged, {"suffix": "Sfx"})
    assert info.value.code == "row_shape"


def test_map_columns_missing_and_duplicate_headers() -> None:
    with pytest.raises(ExtractError) as info:
        map_columns(GRID, {"rate": "Rate"})
    assert info.value.code == "column_missing"
    dup = Grid(header=["Balance", "Balance"], rows=[])
    with pytest.raises(ExtractError) as info:
        map_columns(dup, {"balance": "Balance"})
    assert info.value.code == "column_ambiguous"
