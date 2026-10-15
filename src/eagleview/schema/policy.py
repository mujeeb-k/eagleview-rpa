"""Safety policy: where the browser may go, what actors may do, what counts as irreversible, and
which text patterns are sensitive."""

import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eagleview.schema.action import ActionType
from eagleview.schema.common import Name, Regex, SchemaVersion

_STRICT = ConfigDict(extra="forbid")

# Lowercase scheme://host[:port], with no path, query or userinfo.
_ORIGIN = re.compile(r"https?://(?:[a-z0-9.-]+|\[[0-9a-f:]+\])(?::\d{1,5})?")

HttpMethod = Literal["*", "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


class RouteRule(BaseModel):
    """`pattern` is a path glob: `*` matches within one segment, `/**` matches the path and
    everything below it. Matching is case-insensitive, on the percent-decoded, normalized path."""

    model_config = _STRICT
    pattern: str = Field(pattern=r"^/")
    methods: list[HttpMethod] = Field(default_factory=lambda: list[HttpMethod](["*"]), min_length=1)
    effect: Literal["deny", "irreversible"]


class ActionTypes(BaseModel):
    model_config = _STRICT
    allow: list[ActionType]
    deny: list[ActionType] = Field(default_factory=list[ActionType])

    @model_validator(mode="after")
    def _disjoint(self) -> Self:
        both = set(self.allow) & set(self.deny)
        if both:
            raise ValueError(f"action types both allowed and denied: {sorted(both)}")
        return self


class IrreversibleHandling(BaseModel):
    model_config = _STRICT
    # Discovery never commits on its own: it blocks and escalates for a human approval.
    discovery: Literal["escalate", "block"] = "escalate"
    replay: Literal["require_approval", "block"] = "require_approval"


class SensitivityPattern(BaseModel):
    """A regex for sensitive text. When the regex has a group named `v`, only that group is
    replaced, so a label such as `Member #` survives while the number does not."""

    model_config = _STRICT
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    regex: Regex
    luhn: bool = False  # the match must pass the Luhn check (card numbers)
    ignore_case: bool = False


class Policy(BaseModel):
    model_config = _STRICT
    schema_version: SchemaVersion
    id: Name
    # tenant slug -> origins (scheme://host:port). Origins are per tenant: a run for one tenant
    # that reaches another tenant's origin is off its allowlist.
    allowed_origins: dict[Name, list[str]] = Field(min_length=1)
    routes: list[RouteRule] = Field(default_factory=list[RouteRule])
    action_types: ActionTypes
    allowed_keys: list[str] = Field(default_factory=list[str])
    irreversible_label_patterns: list[str] = Field(default_factory=list[str])
    irreversible_handling: IrreversibleHandling = Field(default_factory=IrreversibleHandling)
    sensitivity_patterns: list[SensitivityPattern] = Field(default_factory=list[SensitivityPattern])

    @model_validator(mode="after")
    def _origins_are_origins(self) -> Self:
        for tenant, origins in self.allowed_origins.items():
            for origin in origins:
                if not _ORIGIN.fullmatch(origin):
                    raise ValueError(f"{tenant}: {origin!r} is not a scheme://host[:port] origin")
        return self
