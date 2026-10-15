"""The capability artifact: a typed, versioned, replayable contract for one task in one app."""

from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag, model_validator

from eagleview.schema.common import (
    UNVERIFIED_SOURCES,
    CapabilityId,
    Code,
    FramePath,
    Name,
    Regex,
    RunId,
    SchemaVersion,
    SemVer,
    Sensitivity,
    Slug,
    Source,
    VersionRange,
)
from eagleview.schema.locators import TargetSpec
from eagleview.schema.predicates import (
    AnyPredicate,
    FieldValue,
    OutputPresent,
    Predicate,
    walk_predicate,
)
from eagleview.schema.templating import (
    TemplateError,
    iter_strings,
    parse_when,
    template_refs,
    when_refs,
)

_STRICT = ConfigDict(extra="forbid")

CapabilityStatus = Literal["draft", "verified", "approved", "deprecated"]
CapabilityRisk = Literal["read_only", "write_staged", "irreversible"]
StepRisk = Literal["read", "write_staged", "irreversible"]

# A capability's risk is the maximum over its steps, on this scale.
_STEP_TO_CAPABILITY_RISK: dict[StepRisk, CapabilityRisk] = {
    "read": "read_only",
    "write_staged": "write_staged",
    "irreversible": "irreversible",
}
_RISK_ORDER: dict[CapabilityRisk, int] = {"read_only": 0, "write_staged": 1, "irreversible": 2}


# --- Contract: inputs and outputs -----------------------------------------------------------------


class InputSpec(BaseModel):
    model_config = _STRICT
    type: Literal["string", "integer", "decimal", "boolean"]
    pattern: Regex | None = None
    enum: list[str] | None = None
    min_length: int | None = Field(default=None, ge=0)
    max_length: int | None = Field(default=None, ge=1)
    required: bool = True
    sensitivity: Sensitivity = Sensitivity.none
    description: str | None = None
    # Synthetic and illustrative only; never a tenant-specific rule.
    example: str | None = None


ScalarType = Literal["string", "integer", "decimal", "boolean"]


class FieldSpec(BaseModel):
    """One field of an array output's items. `sensitivity` defaults to the array's own."""

    model_config = _STRICT
    type: ScalarType
    scale: int | None = Field(default=None, ge=0, le=10)  # decimals: max digits after the point
    pattern: Regex | None = None
    sensitivity: Sensitivity | None = None
    description: str | None = None


class OutputSpec(BaseModel):
    model_config = _STRICT
    type: Literal["string", "integer", "decimal", "boolean", "array"]
    scale: int | None = Field(default=None, ge=0, le=10)
    pattern: Regex | None = None
    sensitivity: Sensitivity = Sensitivity.none
    required: bool = True
    description: str | None = None
    items: dict[Slug, FieldSpec] | None = None

    @model_validator(mode="after")
    def _items_iff_array(self) -> Self:
        if (self.type == "array") != (self.items is not None):
            raise ValueError("`items` is required for arrays and only allowed on arrays")
        if self.scale is not None and self.type != "decimal":
            raise ValueError("`scale` only applies to decimals")
        return self


class Outcome(BaseModel):
    """An expected business answer, returned rather than raised (e.g. MEMBER_NOT_FOUND)."""

    model_config = _STRICT
    code: Code
    kind: Literal["business"] = "business"
    detect: Predicate
    message: str
    source: Source = Source.authored


# --- Steps ----------------------------------------------------------------------------------------


class ClickAction(BaseModel):
    model_config = _STRICT
    type: Literal["click"]


class FillAction(BaseModel):
    model_config = _STRICT
    type: Literal["fill"]
    value: str


class SelectAction(BaseModel):
    """Selects by visible option label, the thing a human reads."""

    model_config = _STRICT
    type: Literal["select"]
    option: str


class PressAction(BaseModel):
    model_config = _STRICT
    type: Literal["press"]
    key: str = Field(min_length=1)


class ExtractAction(BaseModel):
    model_config = _STRICT
    type: Literal["extract"]


class NavigateAction(BaseModel):
    model_config = _STRICT
    type: Literal["navigate"]
    url: str = Field(min_length=1)


StepAction = Annotated[
    ClickAction | FillAction | SelectAction | PressAction | ExtractAction | NavigateAction,
    Field(discriminator="type"),
]

Parser = Literal["text", "money_us", "integer"]


class ScalarExtract(BaseModel):
    model_config = _STRICT
    target: TargetSpec
    parse: Parser = "text"


class TableSpec(BaseModel):
    """A table found under a caption: `columns` maps item fields to header labels."""

    model_config = _STRICT
    anchor_text: str = Field(min_length=1)
    frame: FramePath = Field(default_factory=list[str])
    header_row: bool = True
    columns: dict[Slug, str] = Field(min_length=1)
    parse: dict[Slug, Parser] = Field(default_factory=dict[Slug, Parser])

    @model_validator(mode="after")
    def _parse_keys_are_columns(self) -> Self:
        unknown = set(self.parse) - set(self.columns)
        if unknown:
            raise ValueError(f"parse names unknown columns: {sorted(unknown)}")
        return self


class TableExtract(BaseModel):
    model_config = _STRICT
    table: TableSpec


def _extract_kind(value: Any) -> str:
    if isinstance(value, BaseModel):
        return "table" if isinstance(value, TableExtract) else "scalar"
    return "table" if isinstance(value, dict) and "table" in value else "scalar"


ExtractField = Annotated[
    Annotated[ScalarExtract, Tag("scalar")] | Annotated[TableExtract, Tag("table")],
    Discriminator(_extract_kind),
]


class Step(BaseModel):
    model_config = _STRICT
    id: Slug
    intent: str | None = None
    action: StepAction
    target: TargetSpec | None = None
    extract: dict[Slug, ExtractField] | None = None
    risk: StepRisk = "read"
    idempotent: bool = True
    requires_approval: bool = False
    expect: Predicate | None = None
    timeout_ms: int = Field(default=8000, ge=100, le=600_000)
    when: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        kind = self.action.type
        if kind in ("click", "fill", "select") and self.target is None:
            raise ValueError(f"step {self.id!r}: a {kind} step needs a target")
        if kind in ("extract", "navigate") and self.target is not None:
            raise ValueError(f"step {self.id!r}: a {kind} step takes no target")
        if (kind == "extract") != (self.extract is not None):
            raise ValueError(
                f"step {self.id!r}: `extract` goes with, and only with, an extract action"
            )
        if self.risk == "irreversible":
            if not self.requires_approval:
                raise ValueError(
                    f"step {self.id!r}: irreversible steps need requires_approval: true"
                )
            if self.idempotent:
                raise ValueError(f"step {self.id!r}: irreversible steps cannot be idempotent")
        if self.when is not None:
            try:
                parse_when(self.when)
            except TemplateError as exc:
                raise ValueError(f"step {self.id!r}: {exc}") from exc
        return self


# --- Provenance -----------------------------------------------------------------------------------


class Discovery(BaseModel):
    model_config = _STRICT
    run_id: RunId
    model: str
    at: datetime


class Verification(BaseModel):
    model_config = _STRICT
    run_id: RunId
    at: datetime


class Approval(BaseModel):
    model_config = _STRICT
    reviewer: str = Field(min_length=1)
    at: datetime


class Provenance(BaseModel):
    model_config = _STRICT
    discovered_by: Discovery | None = None
    verified_by: list[Verification] = Field(default_factory=list[Verification])
    approved_by: Approval | None = None
    # sha256 over the canonical serialization, provenance excluded. See schema.hashing.
    content_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")


class AppRef(BaseModel):
    model_config = _STRICT
    profile: Name
    versions: VersionRange


# --- The artifact ---------------------------------------------------------------------------------


class Capability(BaseModel):
    model_config = _STRICT
    schema_version: SchemaVersion
    id: CapabilityId
    version: SemVer
    title: str = Field(min_length=1)
    description: str
    status: CapabilityStatus = "draft"
    app: AppRef
    risk: CapabilityRisk
    requires_session: bool = True
    inputs: dict[Slug, InputSpec] = Field(default_factory=dict[Slug, InputSpec])
    outputs: dict[Slug, OutputSpec] = Field(default_factory=dict[Slug, OutputSpec])
    outcomes: list[Outcome] = Field(default_factory=list[Outcome])
    steps: list[Step] = Field(min_length=1)
    success: Predicate
    provenance: Provenance = Field(default_factory=Provenance)

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    def predicates(self) -> Iterator[AnyPredicate]:
        """Every predicate in the artifact, nested ones included."""
        for outcome in self.outcomes:
            yield from walk_predicate(outcome.detect)
        for step in self.steps:
            if step.expect is not None:
                yield from walk_predicate(step.expect)
        yield from walk_predicate(self.success)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        self._check_step_ids()
        self._check_risk()
        self._check_templates()
        self._check_outputs()
        self._check_status()
        return self

    def _check_step_ids(self) -> None:
        seen: set[str] = set()
        for step in self.steps:
            if step.id in seen:
                raise ValueError(f"duplicate step id {step.id!r}")
            seen.add(step.id)
        codes = [o.code for o in self.outcomes]
        if len(codes) != len(set(codes)):
            raise ValueError("duplicate outcome codes")

    def _check_risk(self) -> None:
        highest: CapabilityRisk = "read_only"
        for step in self.steps:
            risk = _STEP_TO_CAPABILITY_RISK[step.risk]
            if _RISK_ORDER[risk] > _RISK_ORDER[highest]:
                highest = risk
        if self.risk != highest:
            raise ValueError(f"risk is {self.risk!r} but the riskiest step makes it {highest!r}")

    def _check_templates(self) -> None:
        declared = set(self.inputs)
        tree = self.model_dump(mode="json", include={"outcomes", "steps", "success"})
        for text in iter_strings(tree):
            try:
                refs = template_refs(text)
            except TemplateError as exc:
                raise ValueError(str(exc)) from exc
            for _, name in refs:
                if name not in declared:
                    raise ValueError(f"template references undeclared input {name!r}")
        for step in self.steps:
            if step.when is not None:
                missing = when_refs(parse_when(step.when)) - declared
                if missing:
                    raise ValueError(
                        f"step {step.id!r}: when references undeclared {sorted(missing)}"
                    )

    def _check_outputs(self) -> None:
        extracted: set[str] = set()
        for step in self.steps:
            for name, spec in (step.extract or {}).items():
                output = self.outputs.get(name)
                if output is None:
                    raise ValueError(f"step {step.id!r} extracts undeclared output {name!r}")
                if isinstance(spec, TableExtract):
                    if output.type != "array" or output.items is None:
                        raise ValueError(
                            f"output {name!r} is extracted as a table but is not an array"
                        )
                    if set(spec.table.columns) != set(output.items):
                        raise ValueError(f"table columns for {name!r} must match its item fields")
                elif output.type == "array":
                    raise ValueError(f"array output {name!r} must be extracted as a table")
                extracted.add(name)
        for name, output in self.outputs.items():
            if output.required and name not in extracted:
                raise ValueError(f"required output {name!r} is never extracted")
        for pred in self.predicates():
            if isinstance(pred, OutputPresent) and pred.output_present not in self.outputs:
                raise ValueError(f"output_present names undeclared output {pred.output_present!r}")
        for step in self.steps:
            if step.expect is None:
                continue
            for pred in walk_predicate(step.expect):
                if (
                    isinstance(pred, FieldValue)
                    and pred.field_value.target == "self"
                    and step.target is None
                ):
                    raise ValueError(
                        f"step {step.id!r}: field_value target 'self' needs a step target"
                    )
        for outcome in self.outcomes:
            # Outcomes are checked during any step's settle loop, so they cannot refer to a step
            # target or to outputs that have not been extracted yet.
            for pred in walk_predicate(outcome.detect):
                if isinstance(pred, OutputPresent) or (
                    isinstance(pred, FieldValue) and pred.field_value.target == "self"
                ):
                    raise ValueError(f"outcome {outcome.code}: detect must read the screen only")

    def _check_status(self) -> None:
        if self.status in ("verified", "approved"):
            unverified = [o.code for o in self.outcomes if o.source in UNVERIFIED_SOURCES]
            if unverified:
                raise ValueError(f"status {self.status!r} with unverified outcomes: {unverified}")
            if not self.provenance.verified_by:
                raise ValueError(f"status {self.status!r} needs at least one verification run")
        if self.status == "approved" and self.provenance.approved_by is None:
            raise ValueError("status 'approved' needs provenance.approved_by")
