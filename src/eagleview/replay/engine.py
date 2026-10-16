"""The replay engine: runs a capability deterministically, with no LLM anywhere on the path.

A run goes:
1. preflight, before any UI action: normalize and validate inputs, then the status gate;
2. open the app's entry page, read the running version off the screen, check it against the
   capability's and the app profile's ranges, and pin the app fingerprint;
3. sign on with the app profile's login macro, as the harness, with the password resolved from a
   `secret://` reference and registered with the redactor before it is typed;
4. hand the session to automation and run each step: wait for its target to resolve to exactly
   one element, act through `Surface.act`, then settle (see `settle.py`) until the step's
   `expect` holds, a recovery applies, or the step's deadline passes;
5. validate the extracted outputs and check the success predicate.

The rules that matter most:
- Retries happen only on idempotent steps, and nothing is ever retried after an irreversible step
  has been sent. From that moment any result other than success is OUTCOME_UNCERTAIN, for a human
  to check, because posting twice is the worst failure in banking.
- A step whose `expect` already holds before it acts passes only once the screen has visibly
  changed (a document loaded, or the state fingerprint moved). Otherwise a click that did nothing
  would pass on what was already showing.
- Business outcomes are results, not failures: MEMBER_NOT_FOUND and NOT_PERMITTED return
  `status: outcome`.
- The whole run has a deadline. Passing it fails cleanly, naming the step it reached.

Screenshots are buffered in memory and written at the end of the run, after every extracted value
is registered with the redactor, so a value the run read is never written readable."""

import asyncio
import re
import time
from collections import Counter
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from secrets import token_hex
from typing import Literal
from urllib.parse import urljoin, urlsplit

from eagleview.control.interventions import HumanAction, InterventionDesk, LiveSession
from eagleview.control.session_controller import ControlState, SessionController
from eagleview.evidence.writer import EvidenceWriter
from eagleview.replay.preflight import RunOptions, check_inputs, status_gate
from eagleview.replay.render import Rendered, render
from eagleview.replay.settle import Before, Seen, Watcher, pause
from eagleview.safety.policy_engine import ApprovalToken, PolicyContext
from eagleview.safety.secrets import SecretMissing, SecretResolver
from eagleview.schema.action import Action, TextBox
from eagleview.schema.app_profile import (
    AppProfile,
    ClickSequence,
    Dismiss,
    FailSpec,
    GlobalState,
    ReloginAndRestart,
    RetryStepIfIdempotent,
    SelectChoice,
    SelectThenClick,
)
from eagleview.schema.capability import (
    Capability,
    ClickAction,
    ExtractAction,
    FillAction,
    NavigateAction,
    PressAction,
    SelectAction,
    Step,
    TableExtract,
)
from eagleview.schema.common import Source, parse_version, version_in_range
from eagleview.schema.contract import validate_outputs
from eagleview.schema.hashing import content_hash
from eagleview.schema.locators import TargetSpec
from eagleview.schema.overlay import AppliesTo, Overlay
from eagleview.schema.predicates import AnyPredicate
from eagleview.schema.result import (
    Failure,
    FailureCategory,
    FailureEvidence,
    InterventionRecord,
    LocatorReport,
    OutcomeResult,
    OutputValue,
    RecoveryRecord,
    Rejection,
    RejectionCode,
    RunResult,
    RunStatus,
)
from eagleview.schema.templating import evaluate_when, render_template
from eagleview.schema.tenant import TenantProfile
from eagleview.surfaces.base import (
    ActionResult,
    Ambiguous,
    PredicateContext,
    Surface,
    SurfaceOpener,
    Unique,
)
from eagleview.surfaces.capture import capture
from eagleview.surfaces.extract import ExtractError, parse, parse_rows
from eagleview.surfaces.predicates import describe

AUTOMATION = "automation"
HARNESS = "harness"
SYSTEM = "system"
# How long the entry page and the sign-on's success state may take to appear.
OPEN_TIMEOUT_MS = 15_000
SIGN_ON_TIMEOUT_MS = 15_000

# Actions that set a value in place. Their own result proves their effect (fill clears the field
# and types the value), and they change no document or fingerprint, so a value that was already
# there does not need a screen change to pass.
_VALUE_SETTERS = frozenset({"fill", "select"})

_CATEGORY_FOR_STATUS: dict[str, FailureCategory] = {
    "blocked": FailureCategory.POLICY_BLOCKED,
    "needs_approval": FailureCategory.POLICY_BLOCKED,
    "escalate": FailureCategory.POLICY_BLOCKED,
    "not_holder": FailureCategory.POLICY_BLOCKED,
    "target_mismatch": FailureCategory.UNKNOWN_STATE,
    "error": FailureCategory.UNKNOWN_STATE,
}


_DIGITS = re.compile(r"\d")


def _new_text(now: list[TextBox], before: set[str], frame: str | None) -> str | None:
    """The first text a person can read now that was not there before the step: what a human
    saw that automation did not expect. Text that only changed its digits (a countdown, a date)
    is not new. The paused step's own frame is searched first."""
    seen = {_DIGITS.sub("#", t) for t in before}
    fresh = [
        b
        for b in now
        if b.source == "text"
        and not b.covered
        and any(c.isalpha() for c in b.text)
        and _DIGITS.sub("#", b.text) not in seen
    ]
    fresh.sort(key=lambda b: (frame is not None and b.frame != frame,))
    return fresh[0].text if fresh else None


def new_run_id() -> str:
    return f"r_{datetime.now(UTC):%Y%m%dT%H%M%S}_{token_hex(3)}"


class _Done(Exception):
    """Unwinds to the top of the run with its final result."""

    def __init__(self, result: RunResult) -> None:
        super().__init__(result.status)
        self.result = result


class _Restart(Exception):
    """Signed on again: run the flow from its first step."""


class _Retry(Exception):
    """Run the current step again (idempotent steps only)."""


class _Preempted(Exception):
    """An operator took the lease mid-step: wait for them to hand back."""


# Terminal screens a probe run captures: the ones no state or outcome recognized.
_PROBED = frozenset({FailureCategory.UNKNOWN_STATE, FailureCategory.STATE_TIMEOUT})
# Takeovers per run: each needs a human, but a run should not loop through them forever.
MAX_TAKEOVERS = 3
# Failures a human cannot fix by taking over: policy refusals and the takeover's own outcomes.
_NO_TAKEOVER = frozenset(
    {
        FailureCategory.POLICY_BLOCKED,
        FailureCategory.ABORTED_BY_OPERATOR,
        FailureCategory.HANDOFF_STATE_MISMATCH,
    }
)


@dataclass(frozen=True)
class _Shot:
    number: int
    phase: Literal["before", "after"]
    png: bytes
    boxes: list[TextBox]


@dataclass(frozen=True)
class _Run:
    """What every result names, whether or not the UI was ever touched."""

    ref: str  # "<id>@<version>"
    tenant: TenantProfile
    writer: EvidenceWriter
    options: RunOptions
    modified: bool
    effective_hash: str | None
    started: float

    @property
    def run_id(self) -> str:
        return self.writer.run_id

    def event(self, kind: str, **fields: object) -> None:
        self.writer.event(kind, SYSTEM, **fields)

    def result(
        self,
        status: RunStatus,
        *,
        outputs: dict[str, OutputValue] | None = None,
        outcome: OutcomeResult | None = None,
        rejection: Rejection | None = None,
        failure: Failure | None = None,
        recoveries: list[RecoveryRecord] | None = None,
        interventions: list[InterventionRecord] | None = None,
        locator_report: list[LocatorReport] | None = None,
        timing_ms: dict[str, int] | None = None,
    ) -> RunResult:
        timing = dict(timing_ms or {})
        timing["total"] = int((time.monotonic() - self.started) * 1000)
        return RunResult(
            run_id=self.run_id,
            capability=self.ref,
            effective_hash=self.effective_hash,
            tenant=self.tenant.slug,
            status=status,
            outputs=outputs,
            outcome=outcome,
            rejection=rejection,
            failure=failure,
            recoveries=recoveries or [],
            interventions=interventions or [],
            locator_report=locator_report or [],
            timing_ms=timing,
            modified=True if self.modified else None,
        )

    def end(self, result: RunResult) -> RunResult:
        self.event("run.end", status=result.status, timing_ms=result.timing_ms)
        self.writer.summary(result)
        return result


async def replay(
    capability: Capability,
    raw_inputs: Mapping[str, object],
    *,
    profile: AppProfile,
    tenant: TenantProfile,
    open_surface: SurfaceOpener,
    writer: EvidenceWriter,
    environ: Mapping[str, str] | None = None,
    options: RunOptions | None = None,
    modified: bool = False,
    desk: InterventionDesk | None = None,
) -> RunResult:
    """Run `capability` once on `tenant` and return its RunResult. Never raises for anything
    that happens during the run; a misconfigured call (the wrong app profile) is a ValueError.

    With a `desk`, a human can be brought in: `escalate` blocks on a takeover instead of parking,
    and a gated step without `approve_gates` asks for an approval. Without one, P4 behaviour."""
    if capability.app.profile != profile.id:
        raise ValueError(f"{capability.ref} runs on {capability.app.profile!r}, not {profile.id!r}")
    if tenant.app_profile != profile.id:
        raise ValueError(f"tenant {tenant.slug!r} runs {tenant.app_profile!r}, not {profile.id!r}")
    opts = options or RunOptions()
    if opts.probe and desk is not None:
        raise ValueError("a probe run never has an intervention desk: it must not be approvable")
    base = _Run(
        capability.ref,
        tenant,
        writer,
        opts,
        modified,
        content_hash(capability),
        time.monotonic(),
    )
    writer.redactor.use_capability(capability)
    writer.use_screen_labels(profile.sensitive_labels)
    base.event(
        "run.start",
        capability=capability.ref,
        tenant=tenant.slug,
        effective_hash=base.effective_hash,
        artifact_status=capability.status,
        unattended=opts.unattended,
        approve_gates=opts.approve_gates,
        escalate=opts.escalate,
        deadline_s=opts.deadline_s,
        modified=modified,
    )

    inputs, errors = check_inputs(capability, raw_inputs)
    if errors:
        rejection = Rejection(code=RejectionCode.INPUT_INVALID, errors=errors)
        return base.end(base.result("rejected", rejection=rejection))
    gate = status_gate(capability, opts, modified=modified)
    if gate is not None:
        return base.end(base.result("rejected", rejection=gate))
    if capability.status in ("draft", "deprecated"):
        base.event("run.warning", detail=f"running a {capability.status} capability, attended")
    writer.redactor.register_inputs(inputs)

    controller = SessionController(writer.run_id, sink=writer, actor=HARNESS)
    secrets = SecretResolver(environ, on_resolve=writer.redactor.register_secret)
    ctx = PolicyContext(tenant=tenant.slug, mode="replay", run_id=writer.run_id)
    async with open_surface(controller, ctx) as surface:
        engine = _Engine(
            base, capability, profile, surface, controller, secrets, dict(inputs), desk
        )
        if desk is not None:
            desk.attach(LiveSession(writer.run_id, capability.ref, surface, controller))
        try:
            result = await engine.run()
        finally:
            if desk is not None:
                desk.detach(writer.run_id)
        engine.flush_evidence()
    return base.end(result)


class _Engine:
    def __init__(
        self,
        base: _Run,
        capability: Capability | None,
        profile: AppProfile,
        surface: Surface,
        controller: SessionController,
        secrets: SecretResolver,
        inputs: dict[str, object],
        desk: InterventionDesk | None = None,
    ) -> None:
        self.base = base
        self.cap = capability  # None: only open and sign on (discovery)
        self.tenant = base.tenant
        self.options = base.options
        self.profile = profile
        self.surface = surface
        self.ctl = controller
        self.actor = HARNESS  # who the engine acts as: the harness signs on, automation runs steps
        self.desk = desk
        self.secrets = secrets
        self.inputs = inputs
        self.rendered: Rendered | None = render(capability, inputs) if capability else None
        outcomes = self.plan.outcomes if self.rendered else []
        self.watch = Watcher(profile.global_states, outcomes)

        self.outputs: dict[str, object] = {}
        self.recoveries: list[RecoveryRecord] = []
        self.interventions: list[InterventionRecord] = []
        self.locators: dict[str, LocatorReport] = {}
        self.timing: dict[str, int] = {}
        self.spent: Counter[str] = Counter()
        self.at_step: str | None = None
        self.committed: str | None = None  # the irreversible step whose action was sent
        self.sent: set[str] = set()
        self._visible_before: set[str] = set()
        self._paused_frame: str | None = None  # steps whose action was dispatched at least once
        self.takeovers = 0
        self._deadline: asyncio.Timeout | None = None
        self.shots: list[_Shot] = []
        self.doms: list[tuple[str, str]] = []
        self._shot_no = 0

    # --- the run ------------------------------------------------------------------------------

    async def run(self) -> RunResult:
        remaining = self.options.deadline_s - (time.monotonic() - self.base.started)
        try:
            async with asyncio.timeout(remaining) as deadline:
                self._deadline = deadline
                return await self._body()
        except _Done as done:
            return done.result
        except TimeoutError:
            where = f" at step {self.at_step!r}" if self.at_step else " before the first step"
            return await self._fail(
                FailureCategory.STATE_TIMEOUT,
                f"the run deadline of {self.options.deadline_s:g}s passed{where}",
            )
        except Exception as exc:  # never raise across the replay boundary
            self._event("run.error", error=type(exc).__name__, detail=str(exc))
            return await self._fail(
                FailureCategory.UNKNOWN_STATE, f"unexpected {type(exc).__name__}: {exc}"
            )

    async def _body(self) -> RunResult:
        started = time.monotonic()
        await self._open_app()
        self.timing["sign_on"] = int((time.monotonic() - started) * 1000)
        self._hand_to(AUTOMATION)
        steps = self.plan.steps
        i = 0
        while i < len(steps):
            try:
                if self.ctl.state is ControlState.PAUSE_REQUESTED:
                    i = await self._takeover(i, "OPERATOR_PAUSE", None)
                    continue
                await self._run_step(steps[i])
            except _Restart:
                self.outputs.clear()
                i = 0
                continue
            except _Preempted:
                i = await self._takeover(i, "OPERATOR_TAKEOVER", None)
                continue
            except _Done as done:
                failure = done.result.failure
                if not self._may_take_over(done.result) or failure is None:
                    raise
                i = await self._takeover(i, failure.category.value, failure)
                continue
            i += 1
        result = await self._finish()
        if self.ctl.state in (ControlState.AUTOMATION, ControlState.PAUSE_REQUESTED):
            self.ctl.complete()
        return result

    async def _run_step(self, step: Step) -> None:
        if step.when is not None and not evaluate_when(step.when, self.inputs):
            self._event("step.skip", step_id=step.id, when=step.when)
            return
        self.at_step = step.id
        if self.desk is not None:
            self.desk.session(self.base.run_id).at_step = step.id
        self._event("step.start", step_id=step.id, action=step.action.type, risk=step.risk)
        if step.requires_approval:
            await self._approve(step)
        started = time.monotonic()
        try:
            while True:
                try:
                    await self._attempt(step)
                    break
                except _Retry:
                    self._event("step.retry", step_id=step.id)
        finally:
            spent = int((time.monotonic() - started) * 1000)
            self.timing[step.id] = self.timing.get(step.id, 0) + spent
        self._event("step.done", step_id=step.id)

    async def _attempt(self, step: Step) -> None:
        match step.action:
            case ExtractAction():
                await self._extract(step)
                return
            case NavigateAction(url=url):
                action = Action(type="navigate", url=urljoin(self.tenant.base_url + "/", url))
            case ClickAction():
                action = Action(type="click")
            case FillAction(value=value):
                action = Action(type="fill", text=value)
            case SelectAction(option=option):
                action = Action(type="select", option=option)
            case PressAction(key=key):
                action = Action(type="key", key=key)

        for attempt in range(2):
            target: Unique | None = None
            if step.target is not None:
                target = await self._await_target(step.target, step, step.timeout_ms)
                if isinstance(step.action, PressAction):
                    # A press on a target: focus it with a click first, then press.
                    focus = await self._act(Action(type="click"), step, target)
                    if not focus.ok:
                        await self._act_failed(step, focus)
                    target = None
            held = (
                step.expect is not None
                and action.type not in _VALUE_SETTERS
                and await self.surface.check(step.expect, self._pctx(step))
            )
            before = await self._before(held, shot=True)
            result = await self._act(action, step, target)
            if result.status == "target_mismatch" and attempt == 0:
                self._event("step.target_changed", step_id=step.id, reason=result.reason)
                continue  # nothing was sent: resolve again, once
            if not result.ok:
                await self._act_failed(step, result)
            await self._settle(step.expect, step, before, timeout_ms=step.timeout_ms)
            await self._shot()
            return

    async def _act(self, action: Action, step: Step, target: Unique | None) -> ActionResult:
        result = await self.surface.act(
            action, self.actor, self.ctl.epoch, target=target, step_id=step.id
        )
        if result.status in ("done", "error"):
            self.sent.add(step.id)
        if target is not None:
            self.locators[step.id] = LocatorReport(
                step_id=step.id,
                strategy=target.strategy,
                rank=target.rank,
                agreement=target.agreement,
            )
        if step.risk == "irreversible" and result.status in ("done", "error"):
            # "error" can come from the browser after the click was dispatched: treat it as sent.
            self.committed = step.id
            self._event("step.committed", step_id=step.id)
        await self._check_blocked()
        return result

    async def _act_failed(self, step: Step, result: ActionResult) -> None:
        if result.status == "not_holder" and self.desk is not None:
            raise _Preempted()
        category = _CATEGORY_FOR_STATUS.get(result.status, FailureCategory.UNKNOWN_STATE)
        raise _Done(
            await self._fail(
                category,
                f"{result.action.type} was refused or failed ({result.status}): {result.reason}",
                step_id=step.id,
            )
        )

    # --- opening the app and signing on -------------------------------------------------------

    async def _open_app(self) -> None:
        await self._navigate_entry()
        await self._settle(
            self.profile.fingerprint.detect,
            None,
            None,
            timeout_ms=OPEN_TIMEOUT_MS,
            login=True,
            what=f"the entry page does not look like {self.profile.id}",
        )
        version = await self._read_version()
        if version is None:
            raise _Done(
                await self._fail(
                    FailureCategory.APP_VERSION_UNSUPPORTED,
                    "the running version could not be read off the entry page",
                )
            )
        ranges = [(self.profile.id, self.profile.versions)]
        if self.cap is not None:
            ranges.insert(0, (self.cap.ref, self.cap.app.versions))
        for owner, allowed in ranges:
            if not version_in_range(version, allowed):
                raise _Done(
                    await self._fail(
                        FailureCategory.APP_VERSION_UNSUPPORTED,
                        f"{self.tenant.slug} runs {self.profile.id} {version}; "
                        f"{owner} supports {allowed}",
                    )
                )
        major, minor, _ = parse_version(version)
        self.surface.app_fingerprint = f"{self.profile.id}@{major}.{minor}/{self.tenant.slug}"
        self._event("app.version", version=version, fingerprint=self.surface.app_fingerprint)
        await self._sign_on()

    async def _navigate_entry(self) -> None:
        url = urljoin(self.tenant.base_url + "/", self.profile.login.entry_path.lstrip("/"))
        result = await self.surface.act(
            Action(type="navigate", url=url), self.actor, self.ctl.epoch
        )
        await self._check_blocked()
        if not result.ok:
            blocked = result.status in ("blocked", "needs_approval", "escalate", "not_holder")
            raise _Done(
                await self._fail(
                    FailureCategory.POLICY_BLOCKED if blocked else FailureCategory.APP_UNAVAILABLE,
                    f"could not open the entry page ({result.status}): {result.reason}",
                    retryable=not blocked,
                )
            )

    async def _read_version(self) -> str | None:
        probe = self.profile.fingerprint.version
        rx = re.compile(probe.pattern)
        scope = "/".join(probe.frame)
        for box in await self.surface.text_boxes():
            if scope and box.frame != scope:
                continue
            match = rx.search(box.text)
            if match is not None:
                return match.group("version")
        return None

    async def _sign_on(self) -> None:
        login = self.profile.login
        creds = self.tenant.credentials
        try:
            password = self.secrets.resolve(creds.password)
        except SecretMissing as exc:
            raise _Done(await self._fail(FailureCategory.SESSION_LOST, str(exc))) from None
        values: dict[str, Mapping[str, object]] = {
            "credentials": {"username": creds.username, "password": password}
        }
        before: Before | None = None
        for macro in login.steps:
            target = await self._await_target(macro.target, None, macro.timeout_ms, login=True)
            match macro.action:
                case "fill":
                    action = Action(type="fill", text=render_template(macro.value or "", values))
                case "press":
                    action = Action(type="key", key=macro.key)
                case "click":
                    action = Action(type="click")
            before = await self._before(False, shot=False)
            result = await self.surface.act(
                action,
                self.actor,
                self.ctl.epoch,
                target=None if action.type == "key" else target,
                step_id=f"login.{macro.id}",
            )
            await self._check_blocked()
            if not result.ok:
                raise _Done(
                    await self._fail(
                        FailureCategory.SESSION_LOST,
                        f"sign-on step {macro.id!r} failed ({result.status}): {result.reason}",
                    )
                )
            if macro.expect is not None:
                await self._settle(
                    macro.expect, None, before, timeout_ms=macro.timeout_ms, login=True
                )
        await self._settle(
            login.success,
            None,
            before,
            timeout_ms=SIGN_ON_TIMEOUT_MS,
            login=True,
            what="sign-on did not reach its success state",
        )
        self._event("session.signed_on", holder=self.actor)

    async def _relogin(self) -> None:
        self._hand_to(HARNESS)
        await self._navigate_entry()
        await self._settle(
            self.profile.fingerprint.detect, None, None, timeout_ms=OPEN_TIMEOUT_MS, login=True
        )
        await self._sign_on()
        self._hand_to(AUTOMATION)

    # --- waiting ------------------------------------------------------------------------------

    async def _await_target(
        self, spec: TargetSpec, step: Step | None, timeout_ms: int, *, login: bool = False
    ) -> Unique:
        """Wait until the target resolves to exactly one uncovered element. While it doesn't, the
        screen is classified like any settle, so a modal or an error page is reported as itself,
        never as a missing locator."""
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            found = await self.surface.resolve(spec)
            if isinstance(found, Unique) and not found.occluded:
                return found
            await self._check_blocked()
            seen = await self.watch.look(
                self.surface, None, self._pctx(step), with_outcomes=not login
            )
            await self._react(seen, step, login=login)
            if time.monotonic() >= deadline:
                what = spec.description or ", ".join(loc.strategy for loc in spec.locators)
                if isinstance(found, Ambiguous):
                    category = FailureCategory.LOCATOR_AMBIGUOUS
                    message = f"{found.count} elements match the target; replay never picks one"
                elif isinstance(found, Unique):
                    category = FailureCategory.UNKNOWN_STATE
                    message = "the target is covered by something no handler recognizes"
                else:
                    category = FailureCategory.LOCATOR_NOT_FOUND
                    message = f"no locator resolved within {timeout_ms} ms"
                raise _Done(await self._fail(category, message, expected=what))
            await pause()

    async def _settle(
        self,
        expect: AnyPredicate | None,
        step: Step | None,
        before: Before | None,
        *,
        timeout_ms: int,
        login: bool = False,
        what: str = "",
    ) -> None:
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            await self._check_blocked()
            seen = await self.watch.look(
                self.surface, expect, self._pctx(step), with_outcomes=not login
            )
            if seen.kind == "expect" or (seen.kind == "nothing" and expect is None):
                if before is None or not before.expect_held or await self._changed(before):
                    return
            else:
                await self._react(seen, step, login=login)
            if time.monotonic() >= deadline:
                raise _Done(await self._timed_out(expect, before, timeout_ms, what))
            await pause()

    async def _changed(self, before: Before) -> bool:
        if self.surface.navigation_count() != before.navigations:
            return True
        return (await self.surface.observe()).fingerprint != before.fingerprint

    async def _timed_out(
        self, expect: AnyPredicate | None, before: Before | None, timeout_ms: int, what: str
    ) -> RunResult:
        changed = before is None or await self._changed(before)
        if changed:
            category = FailureCategory.UNKNOWN_STATE
            detail = "an unrecognized screen is showing"
        else:
            category = FailureCategory.STATE_TIMEOUT
            detail = "nothing changed"
        message = f"{what + ': ' if what else ''}{detail} after {timeout_ms} ms"
        expected = describe(expect) if expect is not None else None
        return await self._fail(category, message, expected=expected)

    async def _before(self, expect_held: bool, *, shot: bool) -> Before:
        if shot and self.desk is not None:
            # What a person could read before this step, so a takeover can tell what is new.
            self._visible_before = {b.text for b in await self.surface.text_boxes()}
        if shot:
            obs, boxes = await capture(self.surface)
            self._shot_no += 1
            self.shots.append(_Shot(self._shot_no, "before", obs.screenshot, boxes))
        else:
            obs = await self.surface.observe()
        return Before(obs.fingerprint, self.surface.navigation_count(), expect_held)

    async def _shot(self, *, new: bool = False) -> int:
        """An `after` screenshot: for the step that just acted, or under a new number."""
        obs, boxes = await capture(self.surface)
        if new or self._shot_no == 0:
            self._shot_no += 1
        self.shots.append(_Shot(self._shot_no, "after", obs.screenshot, boxes))
        return self._shot_no

    # --- states and recovery ------------------------------------------------------------------

    async def _react(self, seen: Seen, step: Step | None, *, login: bool) -> None:
        """Handle what a look found. Returns only when the run should keep waiting."""
        match seen.kind:
            case "hard":
                assert seen.state is not None
                await self._hard(seen.state, step, login=login)
            case "outcome":
                assert seen.outcome is not None
                raise _Done(await self._outcome(seen.outcome.code, seen.outcome.message))
            case "interstitial":
                assert seen.state is not None
                await self._interstitial(seen.state)
            case _:
                return

    async def _hard(self, state: GlobalState, step: Step | None, *, login: bool) -> None:
        self._event("state.detected", state=state.id, step_id=self.at_step)
        if state.outcome is not None:
            raise _Done(await self._outcome(state.outcome.code, state.outcome.message))
        handler = state.recover
        if not self.committed:
            if isinstance(handler, ReloginAndRestart) and not login and self._spend(state):
                self._recovered(state, handler.type)
                await self._relogin()
                raise _Restart()
            if (
                isinstance(handler, RetryStepIfIdempotent)
                and step is not None
                and step.idempotent
                and self._spend(state)
            ):
                self._recovered(state, handler.type)
                raise _Retry()
        raise _Done(await self._fail_state(state))

    async def _interstitial(self, state: GlobalState) -> None:
        self._event("state.detected", state=state.id, step_id=self.at_step)
        if not self._spend(state):
            raise _Done(await self._fail_state(state, "its handler's budget for this run is spent"))
        handler = state.recover
        ok = False
        if isinstance(handler, Dismiss):
            ok = await self._handler_click(handler.click)
        elif isinstance(handler, SelectThenClick):
            ok = await self._handler_select(handler.select) and await self._handler_click(
                handler.click
            )
        elif isinstance(handler, ClickSequence):
            ok = True
            for spec in handler.clicks:
                ok = ok and await self._handler_click(spec)
        if not ok or handler is None:
            raise _Done(await self._fail_state(state, "its handler could not act"))
        self._recovered(state, handler.type)

    async def _handler_click(self, spec: TargetSpec) -> bool:
        found = await self.surface.resolve(spec)
        if not isinstance(found, Unique) or found.occluded:
            return False
        result = await self.surface.act(
            Action(type="click"),
            self.actor,
            self.ctl.epoch,
            target=found,
            step_id=self.at_step,
        )
        await self._check_blocked()
        return result.ok

    async def _handler_select(self, choice: SelectChoice) -> bool:
        if choice.option is None:
            # Choosing by position needs the surface to list options; tenant overlays bring it.
            self._event("recovery.unsupported", detail="select by option_index")
            return False
        found = await self.surface.resolve(choice.target)
        if not isinstance(found, Unique) or found.occluded:
            return False
        result = await self.surface.act(
            Action(type="select", option=choice.option),
            self.actor,
            self.ctl.epoch,
            target=found,
            step_id=self.at_step,
        )
        return result.ok

    def _spend(self, state: GlobalState) -> bool:
        limit = state.max_per_run or 1
        if self.spent[state.id] >= limit:
            return False
        self.spent[state.id] += 1
        return True

    def _recovered(self, state: GlobalState, handler: str) -> None:
        record = RecoveryRecord(handler=f"{state.id}.{handler}", at_step=self.at_step)
        self.recoveries.append(record)
        self._event("recovery", handler=record.handler, step_id=self.at_step)

    async def _check_blocked(self) -> None:
        blocked = self.surface.drain_blocked()
        if blocked:
            first = blocked[0]
            path = urlsplit(first.url).path
            raise _Done(
                await self._fail(
                    FailureCategory.POLICY_BLOCKED,
                    f"the network guard blocked {first.method} {path} ({first.reason})",
                )
            )

    # --- gates, extraction, the end -----------------------------------------------------------

    async def _approve(self, step: Step) -> None:
        if self.desk is not None and not self.options.approve_gates:
            await self._ask_approval(step)
            return
        if self.options.unattended or not self.options.approve_gates:
            why = (
                "an unattended run never approves its own irreversible steps"
                if self.options.unattended
                else "pass --approve-gates to approve it, or run with the operator console"
            )
            raise _Done(
                await self._fail(
                    FailureCategory.POLICY_BLOCKED,
                    f"step {step.id!r} needs an approval: {why}",
                    step_id=step.id,
                )
            )
        self.surface.grant_approval(ApprovalToken(self.base.run_id, step.id))
        self.interventions.append(
            InterventionRecord(
                id=f"approval.{step.id}", kind="approval", resolution="approved by --approve-gates"
            )
        )
        self._event("approval.granted", step_id=step.id, source="approve_gates")

    async def _ask_approval(self, step: Step) -> None:
        """Block on a human's decision in the console. The run never approves itself."""
        assert self.desk is not None
        screenshot = await self._shot_now()
        iv = self.desk.open(
            kind="approval",
            run_id=self.base.run_id,
            capability=self.base.ref,
            step_id=step.id,
            reason="APPROVAL_REQUIRED",
            state_summary=(await self.surface.observe()).summary(),
            screenshot=screenshot,
            allowed=["approve", "deny"],
        )
        self._event("approval.requested", intervention=iv.id, step_id=step.id)
        async with self._deadline_paused():
            done = await self.desk.wait(iv.id)
        by = done.resolved_by or "system"
        self.interventions.append(
            InterventionRecord(id=iv.id, kind="approval", resolution=f"{done.resolution} by {by}")
        )
        if done.resolution == "approved":
            self.surface.grant_approval(ApprovalToken(self.base.run_id, step.id))
            self._event("approval.granted", step_id=step.id, source=by)
            return
        if done.resolution == "timed_out":
            waited = f"{self.desk.human_timeout_s:g}s"
            raise _Done(
                await self._park(
                    FailureCategory.POLICY_BLOCKED,
                    f"step {step.id!r} needs an approval and nobody answered within {waited}",
                    "approval timed out",
                    step_id=step.id,
                )
            )
        note = f": {done.note}" if done.note else ""
        raise _Done(
            await self._fail(
                FailureCategory.POLICY_BLOCKED,
                f"step {step.id!r} was {done.resolution} by {by}{note}",
                step_id=step.id,
            )
        )

    # --- takeover -----------------------------------------------------------------------------

    def _may_take_over(self, result: RunResult) -> bool:
        failure = result.failure
        return (
            self.desk is not None
            and self.options.escalate
            and result.status == "failed"
            and failure is not None
            and failure.category not in _NO_TAKEOVER
            and self.takeovers < MAX_TAKEOVERS
        )

    async def _takeover(self, i: int, reason: str, failure: Failure | None) -> int:
        """Hand the session to a human and wait for it back. Returns the step index to continue
        from, after resume validation; raises _Done when the run ends instead."""
        assert self.desk is not None
        self.takeovers += 1
        steps = self.plan.steps
        paused = steps[min(i, len(steps) - 1)]
        self._paused_frame = "/".join(paused.target.frame) if paused.target else None
        visible_now = await self.surface.text_boxes()
        screenshot = await self._shot_now()
        summary = (await self.surface.observe()).summary()
        if self.ctl.state is ControlState.PAUSE_REQUESTED:
            self.ctl.yield_to_human()
        elif self.ctl.state is ControlState.AUTOMATION:
            self.ctl.escalate()
        session = self.desk.session(self.base.run_id)
        acted_before = len(session.actions)
        iv = self.desk.open(
            kind="takeover",
            run_id=self.base.run_id,
            capability=self.base.ref,
            step_id=paused.id,
            reason=reason,
            state_summary=summary,
            screenshot=screenshot,
            allowed=["take_control", "resume", "abort"],
        )
        self._event("takeover.requested", intervention=iv.id, reason=reason, step_id=paused.id)
        async with self._deadline_paused():
            ended = await self._wait_for_handback()
        human_actions = session.actions[acted_before:]
        if session.committed and not self.committed:
            self.committed = "operator"
            self._event("step.committed", step_id=paused.id, by="operator")
        if ended is None:
            if self.ctl.state is ControlState.HUMAN:
                self.ctl.abort("system")
            elif self.ctl.state is ControlState.AWAITING_HUMAN:
                self.ctl.fail("system")
            pending = self.desk.store.get(iv.id)
            if pending is not None and pending.resolution is None:
                self.desk.resolve(iv.id, "timed_out", "system")
            self._record_takeover(iv.id, "timed_out", len(human_actions))
            category = failure.category if failure else FailureCategory.UNKNOWN_STATE
            raise _Done(
                await self._park(
                    category,
                    (failure.message if failure and failure.message else reason)
                    + f"; nobody handed back within {self.desk.human_timeout_s:g}s",
                    "takeover timed out",
                )
            )
        done = self.desk.store.get(iv.id)
        by = (done.resolved_by if done else None) or "operator"
        if ended is ControlState.ABORTED:
            self._record_takeover(iv.id, f"aborted by {by}", len(human_actions))
            raise _Done(await self._fail(FailureCategory.ABORTED_BY_OPERATOR, f"aborted by {by}"))
        self.ctl.start_validation()
        resume_at = await self._validate(i)
        if resume_at is None:
            self.ctl.fail()
            self._record_takeover(iv.id, f"resumed by {by}; state mismatch", len(human_actions))
            raise _Done(
                await self._fail(
                    FailureCategory.HANDOFF_STATE_MISMATCH,
                    "after the handback, no step's expected state holds",
                    step_id=paused.id,
                )
            )
        self.ctl.resume_automation()
        self.actor = AUTOMATION
        self._record_takeover(iv.id, f"resumed by {by}", len(human_actions))
        where = steps[resume_at].id if resume_at < len(steps) else "the end"
        self._event("takeover.resumed", intervention=iv.id, resume_at=where)
        self._propose_patch(human_actions, visible_now)
        return resume_at

    async def _wait_for_handback(self) -> ControlState | None:
        assert self.desk is not None
        try:
            async with asyncio.timeout(self.desk.human_timeout_s):
                return await self.ctl.wait_for(
                    {ControlState.RESUME_REQUESTED, ControlState.ABORTED}
                )
        except TimeoutError:
            return None

    async def _validate(self, i: int) -> int | None:
        """Where automation can carry on after a human hands back, or None (PLAN §5).

        Never assume what the human did: look. A hard global state means a mismatch. Otherwise
        continue after the latest step, from the paused one on, whose `expect` holds. Failing
        that, run the paused step again if its target is back and running it again is safe: it
        was never sent, or it is idempotent and nothing is committed."""
        steps = self.plan.steps
        seen = await self.watch.look(self.surface, None, self._pctx(None), with_outcomes=False)
        if seen.kind == "hard":
            return None
        for j in range(len(steps) - 1, i - 1, -1):
            st = steps[j]
            if st.expect is None or (st.when and not evaluate_when(st.when, self.inputs)):
                continue
            if await self.surface.check(st.expect, self._pctx(st)):
                return j + 1
        paused = steps[i]
        safe = paused.id not in self.sent or (paused.idempotent and not self.committed)
        if not safe:
            return None
        if paused.target is None:
            return i if paused.action.type == "extract" else None
        found = await self.surface.resolve(paused.target)
        return i if isinstance(found, Unique) and not found.occluded else None

    def _record_takeover(self, iv_id: str, resolution: str, human_actions: int) -> None:
        self.interventions.append(
            InterventionRecord(
                id=iv_id, kind="takeover", resolution=resolution, human_actions=human_actions
            )
        )

    def _propose_patch(self, actions: list[HumanAction], visible_then: list[TextBox]) -> None:
        """Write what the human did as a draft overlay for a reviewer. Never applied here."""
        clicks = [
            TargetSpec(frame=a.grounded.frame, locators=a.grounded.candidates)
            for a in actions
            if a.action.type == "click" and a.grounded is not None and a.grounded.candidates
        ]
        new_text = _new_text(visible_then, self._visible_before, self._paused_frame)
        if not clicks or new_text is None:
            self._event("patch.skipped", clicks=len(clicks), detect=new_text is not None)
            return
        slug = re.sub(r"[^a-z0-9]+", "_", new_text.casefold()).strip("_")[:40] or "state"
        state = GlobalState(
            id=f"human_{slug}",
            detect={"text_present": {"text": new_text}},  # type: ignore[arg-type]
            recover=ClickSequence(type="click_sequence", clicks=clicks),
            max_per_run=1,
            fail=FailSpec(category=FailureCategory.RECOVERY_EXHAUSTED),
            source=Source.human_demonstrated,
        )
        overlay = Overlay(
            applies_to=AppliesTo(profile=self.profile.id, capabilities=["*"]),
            tenant=self.tenant.slug,
            global_states_add=[state],
            source=Source.human_demonstrated,
        )
        path = self.base.writer.write_yaml("proposed_overlay", overlay)
        self._event("patch.proposed", path=path.name, state=state.id, clicks=len(clicks))

    @asynccontextmanager
    async def _deadline_paused(self) -> AsyncGenerator[None]:
        """Time spent waiting for a human does not count against the run deadline."""
        deadline = self._deadline
        when = deadline.when() if deadline is not None else None
        loop = asyncio.get_running_loop()
        started = loop.time()
        if deadline is not None:
            deadline.reschedule(None)
        try:
            yield
        finally:
            if deadline is not None and when is not None:
                deadline.reschedule(when + (loop.time() - started))

    async def _shot_now(self) -> str:
        """A screenshot written at once (redacted), for an intervention the console shows now."""
        number = await self._shot(new=True)
        shot = self.shots[-1]
        self.base.writer.screenshot(shot.number, shot.phase, shot.png, text_boxes=shot.boxes)
        return f"steps/{number:04d}_after.png"

    async def _park(
        self, category: FailureCategory, message: str, why: str, *, step_id: str | None = None
    ) -> RunResult:
        """Stop and leave the run for a human: `needs_human`, carrying the failure."""
        result = await self._fail(category, message, step_id=step_id, park=False)
        failure = result.failure
        assert failure is not None
        self.interventions.append(
            InterventionRecord(
                id=f"takeover.{failure.step_id or 'run'}",
                kind="takeover",
                resolution=f"parked: {why}",
            )
        )
        return self._result("needs_human", failure=failure)

    async def _extract(self, step: Step) -> None:
        assert step.extract is not None
        for name, spec in step.extract.items():
            try:
                if isinstance(spec, TableExtract):
                    rows = await self.surface.extract(spec.table)
                    if not isinstance(rows, list):
                        raise ExtractError("parse", "expected a table")
                    value: object = parse_rows(rows, spec.table.parse)
                else:
                    text = await self.surface.extract(spec)
                    if not isinstance(text, str):
                        raise ExtractError("parse", "expected a single value")
                    value = parse(text, spec.parse)
            except ExtractError as exc:
                raise _Done(
                    await self._fail(
                        FailureCategory.OUTPUT_INVALID,
                        f"{name}: {exc.code}: {exc}",
                        step_id=step.id,
                    )
                ) from None
            self.outputs[name] = value
            # Registered at once, so every screenshot written later is painted over it.
            self.base.writer.redactor.register_outputs({name: value})
        self._event("step.extracted", step_id=step.id, outputs=sorted(step.extract))

    async def _finish(self) -> RunResult:
        self.at_step = None
        values, errors = validate_outputs(self.plan_capability.outputs, self.outputs)
        if errors:
            problems = "; ".join(f"{e.field}: {e.code}" for e in errors)
            raise _Done(await self._fail(FailureCategory.OUTPUT_INVALID, problems))
        self.base.writer.redactor.register_outputs(values)
        if not await self.surface.check(self.plan.success, PredicateContext(outputs=values)):
            raise _Done(
                await self._fail(
                    FailureCategory.UNKNOWN_STATE,
                    "the success condition does not hold",
                    expected=describe(self.plan.success),
                )
            )
        return self._result("success", outputs=values)

    async def _outcome(self, code: str, message: str) -> RunResult:
        if self.committed:
            return await self._fail(
                FailureCategory.OUTCOME_UNCERTAIN, f"business outcome {code}: {message}"
            )
        self._event("run.outcome", code=code, step_id=self.at_step)
        return self._result(
            "outcome", outcome=OutcomeResult(code=code, message=message, at_step=self.at_step)
        )

    async def _fail_state(self, state: GlobalState, why: str = "") -> RunResult:
        spec = state.fail
        category = spec.category if spec is not None else FailureCategory.UNKNOWN_STATE
        message = f"{state.id} detected" + (f"; {why}" if why else "")
        return await self._fail(category, message, retryable=spec.retryable if spec else False)

    async def _fail(
        self,
        category: FailureCategory,
        message: str,
        *,
        step_id: str | None = None,
        expected: str | None = None,
        retryable: bool = False,
        park: bool = True,
    ) -> RunResult:
        if self.committed and category != FailureCategory.OUTCOME_UNCERTAIN:
            message = f"{category.value} after the irreversible step {self.committed!r}: {message}"
            category = FailureCategory.OUTCOME_UNCERTAIN
        if self.committed:
            message += ". Check the app before trying again: replay never retries after a commit"
            retryable = False
        evidence, observed = await self._capture_failure()
        if self.options.probe and category in _PROBED:
            await self._write_probe(category, step_id or self.at_step)
        failure = Failure(
            category=category,
            step_id=step_id or self.at_step,
            expected=expected,
            observed=observed,
            message=message,
            retryable=retryable,
            evidence=evidence,
        )
        self._event(
            "run.failure", category=category.value, step_id=failure.step_id, message=message
        )
        if park and self.options.escalate and self.desk is None:
            # No console attached: park the run for a human instead of blocking on one.
            self.interventions.append(
                InterventionRecord(
                    id=f"takeover.{failure.step_id or 'run'}",
                    kind="takeover",
                    resolution="parked: no operator console attached",
                )
            )
            return self._result("needs_human", failure=failure)
        return self._result("failed", failure=failure)

    async def _write_probe(self, category: FailureCategory, step_id: str | None) -> None:
        """What a person could read on the unrecognized screen, for outcome classification.
        Written through the writer, so the probe's own input values come out as placeholders."""
        try:
            boxes = await self.surface.text_boxes()
        except Exception as exc:  # best effort, like the rest of the failure evidence
            self._event("probe.error", error=type(exc).__name__)
            return
        texts = [{"text": b.text, "frame": b.frame} for b in boxes if b.source == "text"]
        self.base.writer.write_json(
            "probe", {"step_id": step_id, "category": category.value, "texts": texts}
        )

    async def _capture_failure(self) -> tuple[FailureEvidence, str | None]:
        try:
            number = await self._shot(new=True)
            observed = (await self.surface.observe()).summary()
            self.doms.append(("failure", await self.surface.dom_snapshot()))
        except Exception as exc:  # evidence is best effort; the result must still come back
            self._event("evidence.error", error=type(exc).__name__)
            return FailureEvidence(events="run.jsonl"), None
        return (
            FailureEvidence(
                screenshot=f"steps/{number:04d}_after.png",
                dom_snapshot="dom/failure.redacted.html",
                events="run.jsonl",
            ),
            observed,
        )

    def flush_evidence(self) -> None:
        """Write the buffered screenshots and DOM snapshots, now that every value the run read is
        registered with the redactor."""
        writer = self.base.writer
        for shot in self.shots:
            writer.screenshot(shot.number, shot.phase, shot.png, text_boxes=shot.boxes)
        for name, markup in self.doms:
            writer.dom_snapshot(name, markup)

    # --- helpers ------------------------------------------------------------------------------

    def _result(
        self,
        status: RunStatus,
        *,
        outputs: dict[str, OutputValue] | None = None,
        outcome: OutcomeResult | None = None,
        failure: Failure | None = None,
    ) -> RunResult:
        return self.base.result(
            status,
            outputs=outputs,
            outcome=outcome,
            failure=failure,
            recoveries=self.recoveries,
            interventions=self.interventions,
            locator_report=list(self.locators.values()),
            timing_ms=self.timing,
        )

    @property
    def plan(self) -> Rendered:
        assert self.rendered is not None, "this engine only opens a session"
        return self.rendered

    @property
    def plan_capability(self) -> Capability:
        assert self.cap is not None, "this engine only opens a session"
        return self.cap

    async def prepare(self) -> RunResult | None:
        """Open the app, check its version and sign on, as `run()` does first. None when the
        session is ready; otherwise the failed result."""
        remaining = self.options.deadline_s - (time.monotonic() - self.base.started)
        try:
            async with asyncio.timeout(remaining):
                await self._open_app()
                return None
        except _Done as done:
            return done.result
        except TimeoutError:
            return await self._fail(FailureCategory.STATE_TIMEOUT, "sign-on took too long")

    def _pctx(self, step: Step | None) -> PredicateContext:
        return PredicateContext(
            self_target=step.target if step is not None else None, outputs=self.outputs
        )

    def _hand_to(self, actor: str) -> None:
        self.ctl.hand_to(actor)
        self.actor = actor

    def _event(self, kind: str, **fields: object) -> None:
        self.base.event(kind, **fields)


class SessionNotReady(Exception):
    """The app could not be opened and signed on. Carries the failure, as replay reports it."""

    def __init__(self, failure: Failure) -> None:
        super().__init__(f"{failure.category}: {failure.message}")
        self.failure = failure


@dataclass
class OpenSession:
    surface: Surface
    controller: SessionController
    engine: "_Engine"


@asynccontextmanager
async def open_session(
    *,
    profile: AppProfile,
    tenant: TenantProfile,
    open_surface: SurfaceOpener,
    writer: EvidenceWriter,
    environ: Mapping[str, str] | None = None,
    actor: str = "llm",
    mode: Literal["discovery", "replay"] = "discovery",
    desk: InterventionDesk | None = None,
) -> AsyncGenerator[OpenSession]:
    """A signed-on session for an actor other than replay (discovery's `llm`). The harness opens
    the app, checks its version and signs on exactly as replay does; the session is handed to
    `actor` only afterwards, so the LLM never sees a credential. Evidence is flushed on exit."""
    writer.use_screen_labels(profile.sensitive_labels)
    base = _Run("session.open@0.0.0", tenant, writer, RunOptions(), False, None, time.monotonic())
    controller = SessionController(writer.run_id, sink=writer, actor=HARNESS)
    secrets = SecretResolver(environ, on_resolve=writer.redactor.register_secret)
    ctx = PolicyContext(tenant=tenant.slug, mode=mode, run_id=writer.run_id)
    async with open_surface(controller, ctx) as surface:
        engine = _Engine(base, None, profile, surface, controller, secrets, {}, desk)
        try:
            failed = await engine.prepare()
            if failed is not None and failed.failure is not None:
                raise SessionNotReady(failed.failure)
            engine._hand_to(actor)  # pyright: ignore[reportPrivateUsage]
            yield OpenSession(surface, controller, engine)
        finally:
            engine.flush_evidence()
