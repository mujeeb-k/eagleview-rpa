"""Shared types: slugs, sensitivity, provenance sources, money, frame paths and version ranges."""

import re
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BeforeValidator,
    PlainSerializer,
    StringConstraints,
    WithJsonSchema,
)
from pydantic_core import PydanticSerializationUnexpectedValue

SLUG_PATTERN = r"^[a-z][a-z0-9_]*$"

# A semantic identifier: `enter_member_id`, never an index. Tenant overlays key on these.
Slug = Annotated[str, StringConstraints(pattern=SLUG_PATTERN)]

# `member.lookup_share_balances`: dotted slugs, at least two parts.
CapabilityId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")]

# Kebab-case names for app profiles, tenants and policies: `ironbridge-teller`, `lantern-bay`.
Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]*$")]

SemVer = Annotated[str, StringConstraints(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")]

# `r_` plus URL-safe characters. Run ids become directory names, so nothing else is allowed.
RunId = Annotated[str, StringConstraints(pattern=r"^r_[A-Za-z0-9_-]{1,64}$")]

Code = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]*$")]

SurfaceKind = Literal["web", "legacy_web", "desktop"]

# Artifact schema versions are "MAJOR.MINOR". This build reads major 1; a different major means
# the file was written for an incompatible Eagleview and is refused with a clear message.
SUPPORTED_SCHEMA_MAJOR = 1


class UnsupportedSchemaVersion(ValueError):
    pass


def check_schema_version(value: object) -> str:
    if isinstance(value, int | float) and not isinstance(value, bool):
        # Unquoted in YAML. The major is still readable, and an unsupported one says so first.
        if int(value) != SUPPORTED_SCHEMA_MAJOR:
            raise UnsupportedSchemaVersion(
                f"unsupported schema_version '{value}': this Eagleview reads "
                f"{SUPPORTED_SCHEMA_MAJOR}.x artifacts only"
            )
        raise ValueError(f"schema_version must be a quoted 'MAJOR.MINOR' string, got {value!r}")
    if not isinstance(value, str) or re.fullmatch(r"\d+\.\d+", value) is None:
        raise ValueError(f"schema_version must be a quoted 'MAJOR.MINOR' string, got {value!r}")
    text = value
    major = int(text.split(".")[0])
    if major != SUPPORTED_SCHEMA_MAJOR:
        raise UnsupportedSchemaVersion(
            f"unsupported schema_version {text!r}: this Eagleview reads "
            f"{SUPPORTED_SCHEMA_MAJOR}.x artifacts only"
        )
    return text


SchemaVersion = Annotated[
    str,
    BeforeValidator(check_schema_version),
    WithJsonSchema({"type": "string", "pattern": r"^1\.\d+$"}),
]


class Sensitivity(StrEnum):
    none = "none"
    identifier = "identifier"
    pii = "pii"
    secret = "secret"


class Source(StrEnum):
    """Who contributed an element. Anything an LLM proposed stays `llm_proposed` until a
    deterministic replay verifies it; humans' takeover patches start as `human_demonstrated`."""

    authored = "authored"
    llm_proposed = "llm_proposed"
    human_demonstrated = "human_demonstrated"
    probed = "probed"
    probed_verified = "probed_verified"


UNVERIFIED_SOURCES = frozenset({Source.llm_proposed, Source.human_demonstrated, Source.probed})


def _to_frame_path(value: object) -> object:
    # Predicates usually name one frame (`frame: main`); targets give a path (`frame: [nav]`).
    # Both normalize to a path, so there is one representation in memory and on disk.
    if isinstance(value, str):
        return [value]
    return value


FramePath = Annotated[
    list[Annotated[str, StringConstraints(min_length=1)]], BeforeValidator(_to_frame_path)
]


def _to_text_list(value: object) -> object:
    if isinstance(value, str):
        return [value]
    return value


# Label text with aliases. A bare string is a one-element list.
TextList = Annotated[
    list[Annotated[str, StringConstraints(min_length=1)]], BeforeValidator(_to_text_list)
]


def _check_regex(value: str) -> str:
    try:
        re.compile(value)
    except re.error as exc:
        raise ValueError(f"invalid regular expression: {exc}") from exc
    return value


Regex = Annotated[str, AfterValidator(_check_regex)]


# --- Money ----------------------------------------------------------------------------------------

_MONEY_PATTERN = r"^-?\d+(\.\d+)?$"


def _to_decimal(value: object) -> Decimal:
    # Money never passes through float: a float has already lost the exact cents.
    if isinstance(value, bool | float):
        raise ValueError("money must be a Decimal or a decimal string, never a float")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        if not re.fullmatch(_MONEY_PATTERN, value.strip()):
            raise ValueError(f"not a decimal string: {value!r}")
        try:
            result = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"not a decimal string: {value!r}") from exc
    else:
        raise ValueError(f"money must be a Decimal or a decimal string, got {type(value).__name__}")
    if not result.is_finite():
        raise ValueError("money must be finite")
    return result


def _money_str(value: object) -> str:
    # Raising "unexpected value" lets a union containing Money try its other members.
    if not isinstance(value, Decimal):
        raise PydanticSerializationUnexpectedValue(f"not a Decimal: {type(value).__name__}")
    return str(value)


Money = Annotated[
    Decimal,
    BeforeValidator(_to_decimal),
    PlainSerializer(_money_str, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "pattern": _MONEY_PATTERN}),
]


# --- Version ranges -------------------------------------------------------------------------------

_COMPARATOR = re.compile(r"^(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+){0,2})$")


def parse_version(text: str) -> tuple[int, int, int]:
    """`7.2` -> (7, 2, 0); `7.2.3` -> (7, 2, 3). Missing parts are zero."""
    match = re.fullmatch(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", text.strip())
    if match is None:
        raise ValueError(f"not a version: {text!r}")
    major, minor, patch = match.groups()
    return int(major), int(minor or 0), int(patch or 0)


def _parse_range(text: str) -> list[tuple[str, tuple[int, int, int]]]:
    clauses: list[tuple[str, tuple[int, int, int]]] = []
    for raw in text.split(","):
        match = _COMPARATOR.match(raw.strip())
        if match is None:
            raise ValueError(f"not a version comparator: {raw.strip()!r} (in {text!r})")
        clauses.append((match.group(1), parse_version(match.group(2))))
    return clauses


def version_in_range(version: str, version_range: str) -> bool:
    """True when `version` satisfies every comparator in `version_range`, e.g. ">=7.2,<8.0"."""
    v = parse_version(version)
    for op, bound in _parse_range(version_range):
        ok = {
            ">=": v >= bound,
            "<=": v <= bound,
            ">": v > bound,
            "<": v < bound,
            "==": v == bound,
            "!=": v != bound,
        }[op]
        if not ok:
            return False
    return True


def _check_range(value: str) -> str:
    _parse_range(value)
    return value


VersionRange = Annotated[str, AfterValidator(_check_range)]
