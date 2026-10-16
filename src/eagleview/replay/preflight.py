"""Checks made before the UI is touched: run options, input normalization and validation, and
the status gate. A failure here is the caller's, and the browser never opens."""

from collections.abc import Mapping
from dataclasses import dataclass

from eagleview.schema.capability import Capability
from eagleview.schema.contract import validate_inputs
from eagleview.schema.result import FieldError, Rejection, RejectionCode, Scalar

DEFAULT_DEADLINE_S = 120.0


@dataclass(frozen=True)
class RunOptions:
    """How a run may behave.

    `unattended` is set by callers with no human at hand (the agent catalog always sets it). Such
    a run needs an `approved` capability and may never approve its own irreversible steps.
    `approve_gates` is an attended operator's standing approval for this run's gated steps.
    `escalate` parks a hard failure for a human (`needs_human`) instead of returning `failed`.
    `deadline_s` bounds the whole run, sign-on included."""

    unattended: bool = False
    approve_gates: bool = False
    escalate: bool = False
    deadline_s: float = DEFAULT_DEADLINE_S

    def __post_init__(self) -> None:
        if self.unattended and self.approve_gates:
            raise ValueError(
                "approve_gates cannot be combined with unattended: an unattended run never "
                "approves its own irreversible steps"
            )
        if self.deadline_s <= 0:
            raise ValueError("deadline_s must be positive")


def normalize_inputs(raw: Mapping[str, object]) -> dict[str, object]:
    """Trim surrounding whitespace from strings. Nothing else: no case folding, no digit
    extraction, no padding. `" 10482 "` becomes `"10482"`; `"10 482"` stays as it is and fails
    validation."""
    return {k: v.strip() if isinstance(v, str) else v for k, v in raw.items()}


def check_inputs(
    capability: Capability, raw: Mapping[str, object]
) -> tuple[dict[str, Scalar], list[FieldError]]:
    return validate_inputs(capability.inputs, normalize_inputs(raw))


def status_gate(capability: Capability, options: RunOptions, *, modified: bool) -> Rejection | None:
    if not options.unattended:
        return None
    if modified:
        return Rejection(
            code=RejectionCode.CAPABILITY_NOT_APPROVED,
            message="an edited artifact (--allow-modified) never runs unattended",
        )
    if capability.status != "approved":
        return Rejection(
            code=RejectionCode.CAPABILITY_NOT_APPROVED,
            message=f"unattended runs need an approved capability; this one is {capability.status}",
        )
    return None
