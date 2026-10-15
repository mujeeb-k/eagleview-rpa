"""Log events: JSONL lines `{ts, run_id, seq, actor, kind, ...}`, written by evidence.writer."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from eagleview.schema.action import Actor
from eagleview.schema.common import RunId

# Fields the writer generates itself. They carry no user data, so the pattern layer skips them.
STRUCTURAL_FIELDS = frozenset({"ts", "run_id", "seq", "actor", "kind"})


class Event(BaseModel):
    # Kind-specific payload fields sit beside the structural ones.
    model_config = ConfigDict(extra="allow")
    ts: datetime
    run_id: RunId
    seq: int = Field(ge=0)
    actor: Actor
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
