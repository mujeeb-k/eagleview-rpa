"""The run result: the replay contract a caller (human, script or agent) receives.

`rejected` is the caller's fault, found before any UI action. `outcome` is a legitimate business
answer. `failed` is a hard failure with debug detail. `needs_human` is a parked escalation; it
carries the failure that caused it, so the caller knows why the run stopped."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eagleview.schema.common import Code, Money, RunId, Slug

_STRICT = ConfigDict(extra="forbid")

RunStatus = Literal["success", "outcome", "rejected", "failed", "needs_human"]


class FailureCategory(StrEnum):
    """The closed taxonomy. Nothing else crosses the replay boundary."""

    STATE_TIMEOUT = "STATE_TIMEOUT"
    LOCATOR_NOT_FOUND = "LOCATOR_NOT_FOUND"
    LOCATOR_AMBIGUOUS = "LOCATOR_AMBIGUOUS"
    UNKNOWN_STATE = "UNKNOWN_STATE"
    APP_ERROR = "APP_ERROR"
    APP_UNAVAILABLE = "APP_UNAVAILABLE"
    SESSION_LOST = "SESSION_LOST"
    OUTCOME_UNCERTAIN = "OUTCOME_UNCERTAIN"
    RECOVERY_EXHAUSTED = "RECOVERY_EXHAUSTED"
    OUTPUT_INVALID = "OUTPUT_INVALID"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    APP_VERSION_UNSUPPORTED = "APP_VERSION_UNSUPPORTED"
    HANDOFF_STATE_MISMATCH = "HANDOFF_STATE_MISMATCH"
    ABORTED_BY_OPERATOR = "ABORTED_BY_OPERATOR"


class RejectionCode(StrEnum):
    INPUT_INVALID = "INPUT_INVALID"
    CAPABILITY_NOT_APPROVED = "CAPABILITY_NOT_APPROVED"


class FieldError(BaseModel):
    model_config = _STRICT
    field: str
    code: Literal["missing", "unknown", "type", "pattern", "enum", "length", "scale"]
    message: str


class Rejection(BaseModel):
    model_config = _STRICT
    code: RejectionCode
    errors: list[FieldError] = Field(default_factory=list[FieldError])
    message: str | None = None


class OutcomeResult(BaseModel):
    model_config = _STRICT
    code: Code
    message: str
    at_step: Slug | None = None


class FailureEvidence(BaseModel):
    """Paths relative to the run directory. Every file was written through evidence.writer."""

    model_config = _STRICT
    screenshot: str | None = None
    dom_snapshot: str | None = None
    events: str | None = None


class Failure(BaseModel):
    model_config = _STRICT
    category: FailureCategory
    step_id: Slug | None = None
    expected: str | None = None  # the predicate, human-readable
    observed: str | None = None  # a state fingerprint summary
    message: str | None = None
    retryable: bool = False
    evidence: FailureEvidence = Field(default_factory=FailureEvidence)


class RecoveryRecord(BaseModel):
    model_config = _STRICT
    handler: str  # "<global state id>.<handler type>", e.g. system_notice.dismiss
    at_step: Slug | None = None


class InterventionRecord(BaseModel):
    model_config = _STRICT
    id: str
    kind: Literal["takeover", "approval"]
    resolution: str | None = None
    human_actions: int = Field(default=0, ge=0)


class LocatorReport(BaseModel):
    model_config = _STRICT
    step_id: Slug
    strategy: str
    rank: int = Field(ge=0)
    agreement: bool | None = None  # did the next-ranked strategy resolve to the same element?


# Output values are typed. Money is Decimal in memory and a string on the wire.
Scalar = Annotated[str, Field(strict=True)] | bool | int | Money | None
OutputValue = Scalar | list[dict[str, Scalar]]


class RunResult(BaseModel):
    model_config = _STRICT
    run_id: RunId
    capability: str = Field(pattern=r"^[a-z][a-z0-9_.]*@\d+\.\d+\.\d+$")
    effective_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    tenant: str
    status: RunStatus
    outputs: dict[str, OutputValue] | None = None
    outcome: OutcomeResult | None = None
    rejection: Rejection | None = None
    failure: Failure | None = None
    recoveries: list[RecoveryRecord] = Field(default_factory=list[RecoveryRecord])
    interventions: list[InterventionRecord] = Field(default_factory=list[InterventionRecord])
    locator_report: list[LocatorReport] = Field(default_factory=list[LocatorReport])
    timing_ms: dict[str, int] = Field(default_factory=dict[str, int])
    # A verified or approved artifact run as a draft with --allow-modified after it was edited.
    modified: bool | None = None

    @model_validator(mode="after")
    def _status_matches_payload(self) -> Self:
        present = {
            "success": self.outputs is not None,
            "outcome": self.outcome is not None,
            "rejected": self.rejection is not None,
            "failed": self.failure is not None,
        }
        expected = {
            "success": "success",
            "outcome": "outcome",
            "rejected": "rejected",
            "failed": "failed",
            "needs_human": None,
        }[self.status]
        for status, is_present in present.items():
            if status == "failed" and self.status == "needs_human":
                continue  # a parked run keeps the failure that parked it
            if is_present and status != expected:
                raise ValueError(f"a {self.status!r} result cannot carry {status} data")
        if expected is not None and not present[expected]:
            raise ValueError(f"a {self.status!r} result needs its {expected} data")
        return self
