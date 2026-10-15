"""Overlays patch a capability without forking it. A tenant overlay names a tenant; an app-profile
version patch names a version range and no tenant. Resolution order: base capability, then version
patch, then tenant overlay. Resolution itself is P8."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eagleview.schema.app_profile import GlobalState
from eagleview.schema.common import (
    FramePath,
    Name,
    SchemaVersion,
    Slug,
    Source,
    VersionRange,
)
from eagleview.schema.locators import Locator
from eagleview.schema.predicates import Predicate

_STRICT = ConfigDict(extra="forbid")


class AppliesTo(BaseModel):
    model_config = _STRICT
    profile: Name
    versions: VersionRange | None = None
    capabilities: list[str] = Field(default_factory=lambda: ["*"], min_length=1)  # ids or globs


class LocatorListPatch(BaseModel):
    model_config = _STRICT
    prepend: list[Locator] = Field(default_factory=list[Locator])
    append: list[Locator] = Field(default_factory=list[Locator])
    replace: list[Locator] | None = None

    @model_validator(mode="after")
    def _replace_alone(self) -> Self:
        if self.replace is not None and (self.prepend or self.append):
            raise ValueError("replace cannot be combined with prepend or append")
        return self


class TargetPatch(BaseModel):
    model_config = _STRICT
    description: str | None = None
    frame: FramePath | None = None
    locators: LocatorListPatch | None = None


class StepPatch(BaseModel):
    model_config = _STRICT
    target: TargetPatch | None = None
    expect: Predicate | None = None
    timeout_ms: int | None = Field(default=None, ge=100, le=600_000)


class Overlay(BaseModel):
    model_config = _STRICT
    schema_version: SchemaVersion = "1.0"
    applies_to: AppliesTo
    tenant: Name | None = None
    # Consumed by the anchor, text and a11y strategies: base label -> labels this tenant shows.
    label_aliases: dict[str, list[str]] = Field(default_factory=dict[str, list[str]])
    global_states_add: list[GlobalState] = Field(default_factory=list[GlobalState])
    step_patches: dict[Slug, StepPatch] = Field(default_factory=dict[Slug, StepPatch])
    source: Source = Source.authored

    @model_validator(mode="after")
    def _scoped(self) -> Self:
        if self.tenant is None and self.applies_to.versions is None:
            raise ValueError("an overlay names a tenant, or a version range for a version patch")
        return self
