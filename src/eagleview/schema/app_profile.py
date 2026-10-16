"""App profile: what is true of a vendor product regardless of task, shared by every tenant.

Global error handling lives here, authored once per product and inherited by every capability on
every tenant. That is the main reuse lever."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eagleview.schema.common import (
    Code,
    FramePath,
    Name,
    Regex,
    SchemaVersion,
    Slug,
    Source,
    SurfaceKind,
    VersionRange,
)
from eagleview.schema.locators import TargetSpec
from eagleview.schema.predicates import Predicate
from eagleview.schema.result import FailureCategory
from eagleview.schema.templating import TemplateError, iter_strings, template_refs

_STRICT = ConfigDict(extra="forbid")

# Login steps fill credentials, which come from the tenant, never from the profile itself.
LOGIN_TEMPLATE_ROOTS = frozenset({"credentials"})
LOGIN_TEMPLATE_NAMES = frozenset({"username", "password"})


class VersionProbe(BaseModel):
    """Reads the running version off the screen, e.g. a footer `Ironbridge Teller v7.2.3`."""

    model_config = _STRICT
    frame: FramePath = Field(default_factory=list[str])
    pattern: Regex

    @model_validator(mode="after")
    def _has_version_group(self) -> Self:
        if "(?P<version>" not in self.pattern:
            raise ValueError("the version pattern needs a named group (?P<version>...)")
        return self


class Fingerprint(BaseModel):
    model_config = _STRICT
    detect: Predicate
    version: VersionProbe


class MacroStep(BaseModel):
    model_config = _STRICT
    id: Slug
    intent: str | None = None
    action: Literal["click", "fill", "press"]
    value: str | None = None  # fill: a literal or {{ credentials.username|password }}
    key: str | None = None  # press
    target: TargetSpec
    expect: Predicate | None = None
    timeout_ms: int = Field(default=8000, ge=100, le=600_000)

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if (self.action == "fill") != (self.value is not None):
            raise ValueError(f"login step {self.id!r}: `value` goes with, and only with, fill")
        if (self.action == "press") != (self.key is not None):
            raise ValueError(f"login step {self.id!r}: `key` goes with, and only with, press")
        return self


class LoginMacro(BaseModel):
    """Run by the harness before any discovery or replay. The LLM never sees it run."""

    model_config = _STRICT
    entry_path: str = Field(default="/", pattern=r"^/")
    steps: list[MacroStep] = Field(min_length=1)
    success: Predicate

    @model_validator(mode="after")
    def _templates(self) -> Self:
        for text in iter_strings(self.model_dump(mode="json")):
            try:
                refs = template_refs(text, LOGIN_TEMPLATE_ROOTS)
            except TemplateError as exc:
                raise ValueError(str(exc)) from exc
            for _, name in refs:
                if name not in LOGIN_TEMPLATE_NAMES:
                    raise ValueError(
                        f"login macros may only use credentials.username/password, not {name!r}"
                    )
        return self


# --- Global states and their handlers -------------------------------------------------------------


class ReloginAndRestart(BaseModel):
    """Sign on again and restart the flow. Allowed only before an irreversible commit."""

    model_config = _STRICT
    type: Literal["relogin_and_restart"]


class RetryStepIfIdempotent(BaseModel):
    model_config = _STRICT
    type: Literal["retry_step_if_idempotent"]


class Dismiss(BaseModel):
    model_config = _STRICT
    type: Literal["dismiss"]
    click: TargetSpec


class SelectChoice(BaseModel):
    model_config = _STRICT
    target: TargetSpec
    option_index: int | None = Field(default=None, ge=0)
    option: str | None = None

    @model_validator(mode="after")
    def _one_choice(self) -> Self:
        if (self.option_index is None) == (self.option is None):
            raise ValueError("give exactly one of option_index or option")
        return self


class SelectThenClick(BaseModel):
    model_config = _STRICT
    type: Literal["select_then_click"]
    select: SelectChoice
    click: TargetSpec


RecoveryHandler = Annotated[
    ReloginAndRestart | RetryStepIfIdempotent | Dismiss | SelectThenClick,
    Field(discriminator="type"),
]


class FailSpec(BaseModel):
    model_config = _STRICT
    category: FailureCategory
    retryable: bool = False


class OutcomeSpec(BaseModel):
    model_config = _STRICT
    code: Code
    message: str


class GlobalState(BaseModel):
    """A state checked in every settle loop, in list order. It either recovers (within a budget,
    falling back to `fail` when the handler is exhausted or not allowed), fails, or is a business
    outcome."""

    model_config = _STRICT
    id: Slug
    detect: Predicate
    recover: RecoveryHandler | None = None
    max_per_run: int | None = Field(default=None, ge=1)
    fail: FailSpec | None = None
    outcome: OutcomeSpec | None = None
    source: Source = Source.authored

    @model_validator(mode="after")
    def _one_response(self) -> Self:
        if self.outcome is not None and (self.recover is not None or self.fail is not None):
            raise ValueError(f"global state {self.id!r}: an outcome cannot also recover or fail")
        if self.outcome is None and self.recover is None and self.fail is None:
            raise ValueError(f"global state {self.id!r} needs recover, fail or outcome")
        if self.max_per_run is not None and self.recover is None:
            raise ValueError(f"global state {self.id!r}: max_per_run only applies to recover")
        return self


class ScreenLabels(BaseModel):
    """Personal data a screen shows beside its labels, which the evidence writer paints over in
    every screenshot and scrubs from every DOM snapshot, whether or not the run ever reads it.

    `screen` is text that identifies the screen, such as its heading, compared as a whole label
    (case-insensitive, trailing colon ignored). Omitted, the rule applies to every screen."""

    model_config = _STRICT
    screen: str | None = Field(default=None, min_length=1)
    labels: list[str] = Field(min_length=1)


class AppProfile(BaseModel):
    model_config = _STRICT
    schema_version: SchemaVersion
    id: Name
    title: str | None = None
    versions: VersionRange
    surface: SurfaceKind
    fingerprint: Fingerprint
    login: LoginMacro
    global_states: list[GlobalState] = Field(default_factory=list[GlobalState])
    sensitive_labels: list[ScreenLabels] = Field(default_factory=list[ScreenLabels])

    @model_validator(mode="after")
    def _unique_states(self) -> Self:
        ids = [s.id for s in self.global_states]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate global state ids")
        return self
