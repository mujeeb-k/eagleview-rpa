"""Interventions: requests for a human, either to take over a stuck run or to approve a gate."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eagleview.schema.common import Code, RunId, Slug

OperatorAction = Literal["take_control", "resume", "approve", "deny", "abort"]
Resolution = Literal["approved", "denied", "resumed", "aborted", "timed_out"]


class Intervention(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^iv_[A-Za-z0-9_-]{1,64}$")
    kind: Literal["takeover", "approval"]
    run_id: RunId
    capability: str | None = None  # "<id>@<version>" for replay
    goal: str | None = None  # discovery
    step_id: Slug | None = None
    reason: Code
    state_summary: str
    screenshot: str | None = None  # a redacted file written by evidence.writer
    allowed_actions: list[OperatorAction] = Field(min_length=1)
    created_at: datetime
    resolution: Resolution | None = None
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _resolution_complete(self) -> Self:
        if (self.resolution is None) != (self.resolved_at is None):
            raise ValueError("resolution and resolved_at go together")
        if (self.capability is None) == (self.goal is None):
            raise ValueError("an intervention names a capability (replay) or a goal (discovery)")
        return self
