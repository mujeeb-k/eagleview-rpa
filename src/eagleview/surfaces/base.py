"""The Surface seam. Artifacts never mention Playwright: everything above this line speaks in
locators, predicates, pixels and text boxes, and each surface kind implements them its own way.

Every UI action, whether it comes from the LLM, from replay or from a human operator, goes
through `Surface.act()`. That method checks the lease holder and the policy first, and grounds
the exact point it is about to act on immediately before acting."""

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from eagleview.safety.policy_engine import ApprovalToken, Decision, PolicyContext
from eagleview.schema.action import Action, TextBox
from eagleview.schema.capability import ScalarExtract, TableSpec
from eagleview.schema.common import SurfaceKind
from eagleview.schema.locators import Strategy, TargetSpec
from eagleview.schema.predicates import AnyPredicate
from eagleview.schema.surface import GroundedTarget
from eagleview.surfaces.extract import RawValue

# --- control --------------------------------------------------------------------------------------


class NotHolder(Exception):
    """The actor does not hold the session lease at this epoch (a stale or foreign caller)."""


class HolderGuard(Protocol):
    """Who may act right now. P5's SessionController implements this with a lease and a fencing
    epoch; every `act()` asks it first."""

    def assert_holder(self, actor: str, epoch: int) -> None: ...


@dataclass
class FixedHolder:
    """A lease that never moves: one actor at one epoch. Stands in for the SessionController until
    P5, and stays useful for tests and single-actor tools."""

    actor: str
    epoch: int = 1

    def assert_holder(self, actor: str, epoch: int) -> None:
        if actor != self.actor or epoch != self.epoch:
            raise NotHolder(
                f"{actor}@{epoch} does not hold the session ({self.actor}@{self.epoch} does)"
            )


class EventSink(Protocol):
    """Where a surface reports what it did. `EvidenceWriter.event` fits, so everything a surface
    logs is redacted on its way to disk."""

    def event(self, kind: str, actor: str, **fields: object) -> object: ...


# --- observation ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    """One look at the screen. The screenshot stays in memory; only evidence.writer persists it,
    redacted, using `text_boxes`."""

    screenshot: bytes
    width: int
    height: int
    fingerprint: str  # sha256 over frame URLs, title and visible headings, digits normalized
    url: str
    title: str
    frames: tuple[tuple[str, str], ...]  # (frame path, URL path), top frame first
    headings: tuple[str, ...]

    def summary(self) -> str:
        """A short state description for failure reports. Paths and headings only, no values."""
        frames = ", ".join(f"{p or 'top'}={u}" for p, u in self.frames)
        heads = " | ".join(self.headings) or "(no headings)"
        return f"{self.title!r}; frames: {frames}; headings: {heads}"


# --- resolution -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Element:
    """A live element on some surface. `handle` is surface-specific (a Playwright ElementHandle on
    the web) and is only ever passed back to the surface that produced it."""

    handle: Any
    frame: tuple[str, ...]
    tag: str


@dataclass(frozen=True)
class Attempt:
    """How one ranked locator fared."""

    rank: int
    strategy: Strategy
    outcome: Literal["unique", "ambiguous", "not_found", "skipped"]
    count: int = 0
    detail: str = ""


@dataclass(frozen=True)
class Unique:
    element: Element
    strategy: Strategy
    rank: int
    # Did the next locator that also resolved uniquely find the same element? None: none did.
    agreement: bool | None = None
    agreement_strategy: Strategy | None = None
    # Present in the DOM but another element is on top of it (a fake modal). Not actionable.
    occluded: bool = False
    attempts: tuple[Attempt, ...] = ()


@dataclass(frozen=True)
class Ambiguous:
    count: int
    attempts: tuple[Attempt, ...] = ()


@dataclass(frozen=True)
class NotFound:
    attempts: tuple[Attempt, ...] = ()


Resolution = Unique | Ambiguous | NotFound


# --- acting ---------------------------------------------------------------------------------------

ActStatus = Literal[
    "done",  # executed
    "blocked",  # the policy refused it
    "needs_approval",  # irreversible; replay needs an approval token for this step
    "escalate",  # irreversible during discovery; a human must decide
    "target_mismatch",  # what is under the point now is not the element that was resolved
    "not_holder",  # the actor does not hold the lease at this epoch
    "error",  # could not be performed (bad arguments, focus lost, untypeable text)
]


@dataclass(frozen=True)
class ActionResult:
    status: ActStatus
    action: Action
    actor: str
    reason: str = ""
    point: tuple[int, int] | None = None
    grounded: GroundedTarget | None = None
    decision: Decision | None = None
    image: bytes | None = None  # screenshot and zoom only; in memory, never written raw

    @property
    def ok(self) -> bool:
        return self.status == "done"


@dataclass(frozen=True)
class BlockedRequest:
    """A request the network guard refused."""

    method: str
    url: str
    actor: str
    step_id: str | None
    reason: str
    detail: str


@dataclass(frozen=True)
class PredicateContext:
    """What a predicate may refer to beyond the screen: the current step's own target (for
    `field_value: {target: self}`) and the outputs extracted so far (for `output_present`)."""

    self_target: TargetSpec | None = None
    outputs: Mapping[str, object] = field(default_factory=dict[str, object])


class Surface(Protocol):
    kind: SurfaceKind
    # Discovery and operator input set this so each act() also proposes verified locators.
    record_candidates: bool

    @property
    def app_fingerprint(self) -> str | None:
        """`<profile>@<major.minor>/<tenant>` once the running version is known. Point locators
        are valid only while it equals the fingerprint they were recorded under."""
        ...

    @app_fingerprint.setter
    def app_fingerprint(self, value: str | None) -> None: ...

    async def observe(self) -> Observation: ...

    async def act(
        self,
        action: Action,
        actor: str,
        epoch: int,
        *,
        target: Unique | None = None,
        step_id: str | None = None,
    ) -> ActionResult: ...

    async def ground(self, x: int, y: int) -> GroundedTarget: ...

    async def resolve(self, target: TargetSpec) -> Resolution: ...

    async def check(self, pred: AnyPredicate, ctx: PredicateContext | None = None) -> bool: ...

    async def check_all(
        self, preds: Sequence[AnyPredicate], ctx: PredicateContext | None = None
    ) -> list[bool]:
        """Several predicates against one reading of the screen."""
        ...

    async def extract(self, spec: ScalarExtract | TableSpec) -> RawValue: ...

    async def text_boxes(self, *, every: bool = False) -> list[TextBox]:
        """Text a person can see now, with its geometry. `every` adds text that is covered (a
        translucent overlay still shows it) or out of view, for redaction; never for locating."""
        ...

    async def dom_snapshot(self) -> str:
        """The current document(s) as markup, for failure evidence. Raw: only evidence.writer
        may persist it, and it scrubs it first."""
        ...

    def navigation_count(self) -> int:
        """How many documents have loaded, in any frame, since the surface opened. A reload of the
        same page counts, which a state fingerprint alone cannot see."""
        ...

    def drain_blocked(self) -> list[BlockedRequest]:
        """Requests the network guard blocked since the last drain."""
        ...

    def grant_approval(self, token: ApprovalToken) -> None:
        """Let one irreversible request through for this (run, step). The guard spends it once."""
        ...


# Opens a surface for one run, with this holder and policy context, and closes it afterwards.
SurfaceOpener = Callable[[HolderGuard, PolicyContext], AbstractAsyncContextManager[Surface]]
