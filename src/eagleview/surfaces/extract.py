"""Turning what a surface read off the screen into typed values. Surface-agnostic: a surface returns
strings (a scalar, or a table as header plus rows) and everything here is pure.

Money is parsed straight from its text into `Decimal`. It never passes through float."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from eagleview.schema.capability import Parser
from eagleview.surfaces.geometry import expand_aliases, label_key, normalize_text

RawRow = dict[str, str]
RawValue = str | list[RawRow]
Parsed = str | int | Decimal


class ExtractError(Exception):
    """Reading failed. `code` is stable: not_found, ambiguous, occluded, column_missing,
    column_ambiguous, row_shape, parse."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# US money as legacy screens print it. Negatives come in four styles: a leading minus
# (-$1,234.56), parentheses (($1,234.56)), a trailing ledger minus (1,234.56-) and a DR suffix
# (1,234.56 DR). CR marks a credit, which is positive. Thousands separators must be well formed
# and cents are exactly two digits. Two sign markers at once are contradictory and refused.
_MONEY = re.compile(
    r"^(?P<lead>-|\()?\s*\$?\s*(?P<num>\d{1,3}(?:,\d{3})*|\d+)\.(?P<cents>\d{2})"
    r"\s*(?P<trail>-|\))?\s*(?P<drcr>DR|CR)?$",
    re.IGNORECASE,
)
_INTEGER = re.compile(r"^(?P<sign>-)?(?P<num>\d{1,3}(?:,\d{3})+|\d+)$")


def money_us(text: str) -> Decimal:
    raw = normalize_text(text)
    match = _MONEY.fullmatch(raw)
    if match is None:
        raise ExtractError("parse", f"not a US money amount: {raw!r}")
    lead, trail = match.group("lead"), match.group("trail")
    drcr = (match.group("drcr") or "").upper()
    if (lead == "(") != (trail == ")"):
        raise ExtractError("parse", f"unbalanced parentheses: {raw!r}")
    markers = [lead is not None, trail == "-", drcr == "DR"]
    if sum(markers) > 1 or (drcr == "CR" and (lead or trail)):
        raise ExtractError("parse", f"contradictory sign markers: {raw!r}")
    value = Decimal(f"{match.group('num').replace(',', '')}.{match.group('cents')}")
    return -value if any(markers) else value


def integer(text: str) -> int:
    raw = normalize_text(text)
    match = _INTEGER.fullmatch(raw)
    if match is None:
        raise ExtractError("parse", f"not an integer: {raw!r}")
    value = int(match.group("num").replace(",", ""))
    return -value if match.group("sign") else value


def parse(text: str, parser: Parser) -> Parsed:
    match parser:
        case "text":
            return normalize_text(text)
        case "money_us":
            return money_us(text)
        case "integer":
            return integer(text)


@dataclass(frozen=True)
class Grid:
    """A table as read: header cells and body rows, every cell already normalized text."""

    header: list[str]
    rows: list[list[str]]


def map_columns(
    grid: Grid,
    columns: Mapping[str, str],
    aliases: Mapping[str, Sequence[str]] | None = None,
) -> list[RawRow]:
    """Rows keyed by item field, columns found by header label (case-insensitive, trailing colon
    ignored), including any tenant alias of the label. Each label must match exactly one header
    cell. A body row with one cell is a table
    message ("No loan accounts.") and is dropped; any other row whose width differs from the
    header's is an error, never padded or truncated."""
    keys = [label_key(h) for h in grid.header]
    index: dict[str, int] = {}
    for name, label in columns.items():
        wanted = {label_key(t) for t in expand_aliases([label], aliases or {})}
        hits = [i for i, k in enumerate(keys) if k in wanted]
        if not hits:
            raise ExtractError("column_missing", f"no column headed {label!r} in {grid.header}")
        if len(hits) > 1:
            raise ExtractError("column_ambiguous", f"{len(hits)} columns headed {label!r}")
        index[name] = hits[0]
    out: list[RawRow] = []
    for n, row in enumerate(grid.rows):
        if len(row) == 1 and len(grid.header) > 1:
            continue  # a table message spanning every column
        if len(row) != len(grid.header):
            raise ExtractError(
                "row_shape", f"row {n} has {len(row)} cells, the header has {len(grid.header)}"
            )
        out.append({name: row[i] for name, i in index.items()})
    return out


def parse_rows(rows: Sequence[RawRow], parsers: Mapping[str, Parser]) -> list[dict[str, Parsed]]:
    return [
        {name: parse(text, parsers.get(name, "text")) for name, text in row.items()} for row in rows
    ]
