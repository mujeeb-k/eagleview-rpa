"""Who controls the live session: a lease with a fencing epoch, and the state machine from PLAN §5.

Exactly one actor holds the session at a time, or nobody does while it changes hands. Every
transfer increments the epoch, and `Surface.act()` refuses an actor that does not hold the lease
at the current epoch. So "who is in control" is enforced, not agreed: an automation that was
preempted cannot slip in one more click with its old epoch.

    AUTOMATION --pause--> PAUSE_REQUESTED --yield (step boundary)--> AWAITING_HUMAN
    AUTOMATION --escalate--> AWAITING_HUMAN --take--> HUMAN --resume--> RESUME_REQUESTED
    AUTOMATION | PAUSE_REQUESTED --force_take--> HUMAN
    RESUME_REQUESTED --validate--> VALIDATING --> AUTOMATION | FAILED
    any live state --abort--> ABORTED;   AUTOMATION | PAUSE_REQUESTED --complete--> COMPLETED
"""

import asyncio
from collections.abc import Iterable
from enum import StrEnum

from eagleview.surfaces.base import EventSink, NotHolder


class ControlState(StrEnum):
    AUTOMATION = "AUTOMATION"
    PAUSE_REQUESTED = "PAUSE_REQUESTED"
    AWAITING_HUMAN = "AWAITING_HUMAN"
    HUMAN = "HUMAN"
    RESUME_REQUESTED = "RESUME_REQUESTED"
    VALIDATING = "VALIDATING"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"
    FAILED = "FAILED"


S = ControlState
TERMINAL = frozenset({S.COMPLETED, S.ABORTED, S.FAILED})
LIVE = frozenset(S) - TERMINAL

# Every allowed edge. Anything else raises InvalidTransition.
TRANSITIONS: dict[ControlState, frozenset[ControlState]] = {
    S.AUTOMATION: frozenset(
        {S.PAUSE_REQUESTED, S.AWAITING_HUMAN, S.HUMAN, S.COMPLETED, S.ABORTED, S.FAILED}
    ),
    S.PAUSE_REQUESTED: frozenset({S.AWAITING_HUMAN, S.HUMAN, S.COMPLETED, S.ABORTED, S.FAILED}),
    S.AWAITING_HUMAN: frozenset({S.HUMAN, S.ABORTED, S.FAILED}),
    S.HUMAN: frozenset({S.RESUME_REQUESTED, S.ABORTED}),
    S.RESUME_REQUESTED: frozenset({S.VALIDATING, S.ABORTED}),
    S.VALIDATING: frozenset({S.AUTOMATION, S.FAILED, S.ABORTED}),
    S.COMPLETED: frozenset(),
    S.ABORTED: frozenset(),
    S.FAILED: frozenset(),
}

# Who may act in each state. None: nobody, while the session changes hands or after it ends.
AUTOMATION_ACTORS = frozenset({"automation", "harness", "llm"})


class InvalidTransition(Exception):
    pass


class SessionController:
    """The lease for one run's live session. Single event loop: the engine, the console and the
    operator's requests all run in the same process (PLAN §2)."""

    def __init__(self, run_id: str, *, sink: EventSink | None = None, actor: str = "harness"):
        self.run_id = run_id
        self.sink = sink
        self.state = S.AUTOMATION
        self.holder: str | None = actor
        self.epoch = 1
        self._changed = asyncio.Event()

    # --- the lease ----------------------------------------------------------------------------

    def assert_holder(self, actor: str, epoch: int) -> None:
        if self.holder is None or actor != self.holder or epoch != self.epoch:
            current = f"{self.holder}@{self.epoch}" if self.holder else f"nobody@{self.epoch}"
            raise NotHolder(f"{actor}@{epoch} does not hold the session ({current} does)")

    def hand_to(self, actor: str) -> int:
        """Pass the lease between the harness (sign-on) and an automated actor: replay's
        `automation`, or discovery's `llm`."""
        if self.state is not S.AUTOMATION or actor not in AUTOMATION_ACTORS:
            raise InvalidTransition(f"hand_to({actor!r}) is not allowed in {self.state}")
        self.holder = actor
        self.epoch += 1
        self._emit("control.handoff", by=actor)
        self._notify()
        return self.epoch

    # --- transitions --------------------------------------------------------------------------

    def request_pause(self, operator: str) -> None:
        """Cooperative: automation finishes its current step and yields at the boundary."""
        self._move(S.PAUSE_REQUESTED, holder=self.holder, by=operator, bump=False)

    def yield_to_human(self) -> None:
        """Automation, at a step boundary after a pause request."""
        self._require(S.PAUSE_REQUESTED)
        self._move(S.AWAITING_HUMAN, holder=None, by="automation")

    def escalate(self) -> None:
        """Automation is stuck and asks for a human."""
        self._move(S.AWAITING_HUMAN, holder=None, by="automation")

    def take_control(self, operator: str) -> None:
        self._require(S.AWAITING_HUMAN)
        self._move(S.HUMAN, holder=operator, by=operator)

    def force_take(self, operator: str) -> None:
        """Preempt: the lease moves now, and automation's next act is refused."""
        if self.state not in (S.AUTOMATION, S.PAUSE_REQUESTED):
            raise InvalidTransition(f"force take is not allowed in {self.state}")
        self._move(S.HUMAN, holder=operator, by=operator)

    def resume(self, operator: str) -> None:
        self._require(S.HUMAN)
        if operator != self.holder:
            raise InvalidTransition(f"{operator} does not hold the session ({self.holder} does)")
        self._move(S.RESUME_REQUESTED, holder=None, by=operator)

    def start_validation(self) -> None:
        self._move(S.VALIDATING, holder=None, by="automation")

    def resume_automation(self) -> None:
        self._require(S.VALIDATING)
        self._move(S.AUTOMATION, holder="automation", by="automation")

    def abort(self, by: str) -> None:
        self._move(S.ABORTED, holder=None, by=by)

    def fail(self, by: str = "automation") -> None:
        self._move(S.FAILED, holder=None, by=by)

    def complete(self) -> None:
        self._move(S.COMPLETED, holder=None, by="automation")

    async def wait_for(self, states: Iterable[ControlState]) -> ControlState:
        """Until the state is one of `states`. No polling: woken by each transition."""
        wanted = frozenset(states)
        while self.state not in wanted:
            event = self._changed
            await event.wait()
        return self.state

    def snapshot(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "state": self.state.value,
            "holder": self.holder,
            "epoch": self.epoch,
        }

    # --- internals ----------------------------------------------------------------------------

    def _require(self, state: ControlState) -> None:
        if self.state is not state:
            raise InvalidTransition(f"expected {state}, the session is {self.state}")

    def _move(self, to: ControlState, *, holder: str | None, by: str, bump: bool = True) -> None:
        if to not in TRANSITIONS[self.state]:
            raise InvalidTransition(f"{self.state} -> {to} is not allowed")
        before = self.state
        self.state = to
        if bump:
            self.epoch += 1
        self.holder = holder
        self._emit("control.transition", **{"from": before.value, "to": to.value, "by": by})
        self._notify()

    def _notify(self) -> None:
        self._changed.set()
        self._changed = asyncio.Event()

    def _emit(self, kind: str, **fields: object) -> None:
        if self.sink is not None:
            self.sink.event(kind, "system", holder=self.holder, epoch=self.epoch, **fields)
