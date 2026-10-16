"""The discovery loop: Claude drives the signed-on app through the computer toolset, and every
action is grounded and recorded into `runs/<run_id>/trace.jsonl` for the compiler.

Per the Claude API facts in CLAUDE.md:
- one turn may carry a batch of tool calls; they run in order, each through `Surface.act` (the
  policy check), and the batch stops at the first failure: later computer members get the exact
  halt text with `is_error`, later custom tools get `is_error` too;
- results go back in one user message; computer members' results echo `toolset_name`;
- a batch that does not end with a screenshot gets a fresh one on its last result;
- history is append-only: `response.content` is appended verbatim, screenshots are never pruned;
- thinking is adaptive with summarized display, and the summaries are logged as the rationale.

Stop conditions: declare_success; 40 screen-changing actions; 80 tool calls of any kind; 10
minutes; no progress over 4 screen-changing actions; 2 policy blocks; request_human without a
person; a turn that ends without declare_success; a refusal; the spend limits."""

import asyncio
import base64
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import anthropic
import httpx2
from playwright.async_api import Browser
from pydantic import BaseModel, ConfigDict, Field

from eagleview.control.interventions import HumanAction, InterventionDesk, LiveSession
from eagleview.control.session_controller import ControlState
from eagleview.discovery.client import DEFAULT_EFFORT, make_client
from eagleview.discovery.inputs import GoalInputs, extract_inputs
from eagleview.discovery.meter import BudgetExceeded, Meter
from eagleview.discovery.prompts import SYSTEM, goal_message
from eagleview.discovery.tools import (
    HALT_TEXT,
    LOOKING,
    TOOLSET,
    TOOLSET_NAME,
    custom_tools,
    member_actions,
)
from eagleview.evidence.writer import EvidenceWriter
from eagleview.replay.engine import OpenSession, SessionNotReady, new_run_id, open_session
from eagleview.safety.policy_engine import Decision, PolicyEngine
from eagleview.safety.redaction import Redactor
from eagleview.schema.action import Action, TextBox
from eagleview.schema.app_profile import AppProfile
from eagleview.schema.capability import InputSpec, ScalarExtract, TableSpec
from eagleview.schema.common import Sensitivity
from eagleview.schema.locators import TargetSpec
from eagleview.schema.policy import Policy
from eagleview.schema.surface import GroundedTarget
from eagleview.schema.tenant import TenantProfile
from eagleview.surfaces.base import ActionResult, Surface
from eagleview.surfaces.capture import capture
from eagleview.surfaces.extract import ExtractError, money_us
from eagleview.surfaces.web.launch import surface_opener

FALLBACK_BETA = "server-side-fallback-2026-07-01"
QUIET_POLL_S = 0.25
QUIET_MAX_S = 3.0
LLM = "llm"
_POLICY = frozenset({"blocked", "needs_approval", "escalate"})

Status = Literal["success", "failed", "needs_human", "budget_stopped"]


@dataclass
class DiscoveryConfig:
    goal: str
    model: str
    effort: str = DEFAULT_EFFORT
    max_actions: int = 40
    max_tool_calls: int = 80
    max_seconds: float = 600.0
    no_progress_actions: int = 4
    max_policy_blocks: int = 2
    fallbacks: bool = True
    max_tokens: int = 16000


class DiscoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    goal: str
    status: Status
    stop_reason: str
    message: str | None = None
    model: str
    served_models: list[str] = []
    effort: str
    fallbacks: bool
    inputs: dict[str, InputSpec] = {}
    outputs: list[str] = []
    success_marked: bool = False
    turns: int = 0
    actions: int = 0
    tool_calls: int = 0
    policy_blocks: int = 0
    usd: Decimal = Decimal(0)
    seconds: float = 0.0
    # The goal's input values, for the compiler's verify replay. Never serialized or written.
    values: dict[str, str] = Field(default_factory=dict[str, str], exclude=True, repr=False)


class _Stop(Exception):
    def __init__(self, status: Status, reason: str, message: str | None = None) -> None:
        super().__init__(reason)
        self.status: Status = status
        self.reason = reason
        self.message = message


def _image(png: bytes) -> dict[str, Any]:
    data = base64.standard_b64encode(png).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}


def _result(block: Any, content: Any, *, error: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "tool_result", "tool_use_id": block.id, "content": content}
    if getattr(block, "toolset_name", None) == TOOLSET_NAME:
        out["toolset_name"] = TOOLSET_NAME
    if error:
        out["is_error"] = True
    return out


def _decision(decision: Decision | None) -> dict[str, Any] | None:
    if decision is None:
        return None
    return {
        "verdict": decision.verdict,
        "reason": decision.reason,
        "irreversible": decision.irreversible,
        "audit": decision.audit,
    }


_DIGIT = re.compile(r"\d")


def _appeared(
    before: list[TextBox], after: list[TextBox], headings: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Text a person can read after the action that was not there before, with its frame and
    whether it is a heading. The compiler derives expects from it."""
    seen = {(b.text, b.frame) for b in before if not b.covered}
    shapes = set(headings)
    out: list[dict[str, Any]] = []
    for b in after:
        if b.covered or b.source != "text" or (b.text, b.frame) in seen:
            continue
        if not any(c.isalpha() for c in b.text):
            continue
        shape = _DIGIT.sub("#", " ".join(b.text.split()))
        out.append({"text": b.text, "frame": b.frame, "heading": shape in shapes})
        seen.add((b.text, b.frame))
        if len(out) >= 25:
            break
    return out


def _text(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": text}]


async def discover(
    config: DiscoveryConfig,
    *,
    profile: AppProfile,
    tenant: TenantProfile,
    policy: Policy,
    browser: Browser,
    meter: Meter,
    environ: dict[str, str],
    runs_root: Path,
    desk: InterventionDesk | None = None,
    http_client: httpx2.AsyncClient | None = None,
    run_id: str | None = None,
) -> DiscoveryResult:
    writer = EvidenceWriter(runs_root, run_id or new_run_id(), Redactor.from_policy(policy))
    rid = writer.run_id
    started = time.monotonic()
    result = DiscoveryResult(
        run_id=rid,
        goal=config.goal,
        status="failed",
        stop_reason="not_started",
        model=config.model,
        effort=config.effort,
        fallbacks=config.fallbacks,
    )
    writer.event("discovery.start", "system", model=config.model, effort=config.effort)
    loop: _Loop | None = None
    try:
        client = make_client(environ, writer.redactor, http_client=http_client)
        goal = await extract_inputs(client, config.model, config.goal, meter, rid)
        result.inputs = goal.specs
        result.values = dict(goal.values)
        writer.redactor.input_sensitivity = {k: v.sensitivity for k, v in goal.specs.items()}
        writer.redactor.register_inputs(goal.values)
        writer.event(
            "discovery.inputs", "system", declared={k: v.type for k, v in goal.specs.items()}
        )
        opener = surface_opener(browser, PolicyEngine(policy), sink=writer)
        async with open_session(
            profile=profile,
            tenant=tenant,
            open_surface=opener,
            writer=writer,
            environ=environ,
            actor=LLM,
            mode="discovery",
            desk=desk,
        ) as session:
            if desk is not None:
                desk.attach(LiveSession(rid, "discovery", session.surface, session.controller))
            try:
                loop = _Loop(config, client, meter, writer, session, goal, desk, started)
                await loop.run()
            finally:
                if desk is not None:
                    desk.detach(rid)
    except _Stop as stop:
        result.status, result.stop_reason, result.message = stop.status, stop.reason, stop.message
    except BudgetExceeded as exc:
        result.status, result.stop_reason, result.message = "budget_stopped", "budget", str(exc)
    except SessionNotReady as exc:
        result.stop_reason, result.message = "sign_on_failed", str(exc)
    if loop is not None:
        result.outputs = loop.outputs
        result.success_marked = loop.success
        result.turns, result.actions = loop.turns, loop.actions
        result.tool_calls, result.policy_blocks = loop.calls, loop.policy_blocks
        result.served_models = sorted(loop.served)
        result.usd = loop.usd
        result.fallbacks = loop.cfg.fallbacks
    result.seconds = round(time.monotonic() - started, 1)
    writer.event("discovery.end", "system", status=result.status, stop_reason=result.stop_reason)
    writer.summary(result)
    return result


@dataclass
class _Loop:
    cfg: DiscoveryConfig
    client: anthropic.AsyncAnthropic
    meter: Meter
    writer: EvidenceWriter
    session: OpenSession
    goal: GoalInputs
    desk: InterventionDesk | None
    started: float
    messages: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    outputs: list[str] = field(default_factory=list[str])
    success: bool = False
    turns: int = 0
    actions: int = 0
    calls: int = 0
    policy_blocks: int = 0
    usd: Decimal = Decimal(0)
    served: set[str] = field(default_factory=set[str])
    _changes: list[bool] = field(default_factory=list[bool])
    _seq: int = 0
    _shot: int = 0
    _rationale: str | None = None
    _select: GroundedTarget | None = None

    @property
    def surface(self) -> Surface:
        return self.session.surface

    @property
    def run_id(self) -> str:
        return self.writer.run_id

    # --- the conversation ---------------------------------------------------------------------

    async def run(self) -> None:
        self.surface.record_candidates = True
        self.tools = [TOOLSET, *custom_tools(list(self.goal.values))]
        obs = await self.surface.observe()
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": goal_message(self.cfg.goal, self.goal.values)},
                    _image(obs.screenshot),
                ],
            }
        )
        while True:
            if time.monotonic() - self.started > self.cfg.max_seconds:
                raise _Stop("failed", "time_limit")
            response = await self._call()
            self.turns += 1
            self.messages.append({"role": "assistant", "content": response.content})
            thinking = [b.thinking for b in response.content if b.type == "thinking" and b.thinking]
            said = [b.text for b in response.content if b.type == "text" and b.text]
            self._rationale = "\n".join(thinking + said) or None
            self.writer.event(
                "discovery.turn",
                LLM,
                turn=self.turns,
                stop_reason=response.stop_reason,
                served_model=getattr(response, "model", None),
                rationale=self._rationale,
            )
            if response.stop_reason == "refusal":
                details = getattr(response, "stop_details", None)
                raise _Stop("failed", "refusal", str(getattr(details, "category", None)))
            uses = [b for b in response.content if b.type == "tool_use"]
            if not uses:
                reason = "max_tokens" if response.stop_reason == "max_tokens" else "ended"
                raise _Stop("failed", reason, "the model stopped without declare_success")
            self.messages.append({"role": "user", "content": await self._batch(uses)})

    async def _call(self) -> Any:
        try:
            self.meter.before_request(self.run_id)
        except BudgetExceeded as exc:
            raise _Stop("budget_stopped", "budget", str(exc)) from None
        kwargs: dict[str, Any] = {
            "model": self.cfg.model,
            "max_tokens": self.cfg.max_tokens,
            "system": SYSTEM,
            "tools": self.tools,
            "messages": self.messages,
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": self.cfg.effort},
            "cache_control": {"type": "ephemeral"},
        }
        if self.cfg.fallbacks:
            try:
                response = await self.client.beta.messages.create(
                    **kwargs, betas=[FALLBACK_BETA], fallbacks="default"
                )
            except anthropic.BadRequestError as exc:
                # Rejected requests are not billed. Turn fallbacks off once; never loop.
                self.writer.event("discovery.fallbacks_rejected", "system", detail=str(exc))
                self.cfg.fallbacks = False
                return await self._call()
        else:
            response = await self.client.messages.create(**kwargs)
        usd = self.meter.after_response(self.run_id, self.cfg.model, response, "turn")
        self.usd += usd
        self.served.add(str(getattr(response, "model", self.cfg.model)))
        return response

    # --- one batch ----------------------------------------------------------------------------

    async def _batch(self, uses: list[Any]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        failed = False
        for block in uses:
            if failed:
                halt = (
                    HALT_TEXT
                    if getattr(block, "toolset_name", None) == TOOLSET_NAME
                    else "Not executed: an earlier action in this turn failed."
                )
                results.append(_result(block, halt, error=True))
                continue
            self.calls += 1
            if getattr(block, "toolset_name", None) == TOOLSET_NAME:
                result, ok = await self._member(block)
            else:
                result, ok = await self._custom(block)
            results.append(result)
            failed = not ok
            self._check_limits()
        last = uses[-1]
        if not (getattr(last, "toolset_name", None) == TOOLSET_NAME and last.name == "screenshot"):
            content = results[-1]["content"]
            blocks = _text(content) if isinstance(content, str) else list(content)
            blocks.append(_image((await self.surface.observe()).screenshot))
            results[-1]["content"] = blocks
        return results

    def _check_limits(self) -> None:
        if self.actions >= self.cfg.max_actions:
            raise _Stop("failed", "action_limit", f"{self.actions} actions without success")
        if self.calls >= self.cfg.max_tool_calls:
            raise _Stop("failed", "tool_call_limit", f"{self.calls} tool calls")
        if self.policy_blocks >= self.cfg.max_policy_blocks:
            raise _Stop("failed", "policy_blocks", f"{self.policy_blocks} actions were refused")
        window = self._changes[-self.cfg.no_progress_actions :]
        if len(window) == self.cfg.no_progress_actions and not any(window):
            raise _Stop("failed", "no_progress", "the screen did not change")

    async def _member(self, block: Any) -> tuple[dict[str, Any], bool]:
        data: dict[str, Any] = dict(block.input or {})
        planned = member_actions(block.name, data)
        if isinstance(planned, str):
            self._record(block.name, "computer", data, status="error", reason=planned)
            return _result(block, planned, error=True), False
        bound = None
        if block.name == "type":
            bound = next((k for k, v in self.goal.values.items() if v == data.get("text")), None)
            if bound is not None:
                self.writer.event("discovery.auto_bound", "system", param=bound)
        return await self._act(block, block.name, planned, data, bound=bound)

    async def _act(
        self,
        block: Any,
        tool: str,
        planned: list[Action],
        data: dict[str, Any],
        *,
        bound: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        looking = tool in LOOKING
        nav_before = self.surface.navigation_count()
        boxes_before: list[TextBox] = []
        if looking:
            before = await self.surface.observe()
            shot_before = None
        else:
            before, boxes_before = await capture(self.surface)
            shot_before = self._write_shot("before", before.screenshot, boxes_before)
        last: ActionResult | None = None
        for action in planned:
            last = await self.surface.act(action, LLM, self.session.controller.epoch)
            if not last.ok:
                break
        assert last is not None
        handed: list[HumanAction] | None = None
        if last.status == "escalate" and self.desk is not None:
            # Discovery never commits. A person performs the irreversible action (PLAN §5).
            handed = await self._hand_over("APPROVAL_REQUIRED", last.reason)
        elif last.status in _POLICY:
            self.policy_blocks += 1
        after, boxes_after = before, boxes_before
        if not looking:
            await self._quiet()
            after, boxes_after = await capture(self.surface)
        ok = last.ok or handed is not None
        changed = (
            after.fingerprint != before.fingerprint
            or self.surface.navigation_count() != nav_before
            or (ok and planned[-1].type == "type")
        )
        if not looking:
            self.actions += 1
            self._changes.append(changed)
        shot_after = None if looking else self._write_shot("after", after.screenshot, boxes_after)
        grounded = last.grounded
        if grounded is not None and grounded.tag == "select":
            self._select = grounded
        elif tool in ("left_click", "double_click"):
            self._select = None
        self._record(
            tool,
            "computer" if tool != "fill_input" else "custom",
            data,
            status="handed_over" if handed is not None else last.status,
            reason=last.reason,
            grounded=grounded.model_dump(mode="json") if grounded else None,
            decision=_decision(last.decision),
            fingerprint={"before": before.fingerprint, "after": after.fingerprint},
            changed=changed,
            bound=bound,
            appeared=None if looking else _appeared(boxes_before, boxes_after, after.headings),
            select_value=await self._select_value() if not looking else None,
            shots={"before": shot_before, "after": shot_after},
        )
        for human in handed or []:
            self._record(
                {"click": "left_click", "double_click": "double_click"}.get(
                    human.action.type, human.action.type
                ),
                "operator",
                human.action.model_dump(exclude_none=True),
                actor=human.actor,
                status="done",
                grounded=human.grounded.model_dump(mode="json") if human.grounded else None,
                decision=_decision(human.decision),
                fingerprint={"before": before.fingerprint, "after": after.fingerprint},
                changed=changed,
                appeared=_appeared(boxes_before, boxes_after, after.headings),
            )
        if handed is not None:
            done = (
                f"A person performed {len(handed)} action(s) for you, including the one that "
                "needed approval. Look at the screen and carry on."
            )
            return _result(block, done), True
        if not last.ok:
            why = last.reason
            if last.status == "escalate":
                why = "this action needs a person's approval; call request_human if it is required"
            return _result(block, f"{last.status}: {why}", error=True), False
        if last.image is not None:
            return _result(block, [_image(last.image)]), True
        return _result(block, "OK"), True

    async def _select_value(self) -> str | None:
        """The option a select now shows, when the model is working a select."""
        g = self._select
        if g is None or not g.candidates:
            return None
        try:
            value = await self.surface.extract(
                ScalarExtract(target=TargetSpec(frame=g.frame, locators=g.candidates))
            )
        except ExtractError:
            return None
        return value if isinstance(value, str) else None

    async def _hand_over(self, reason: str, detail: str) -> list[HumanAction] | None:
        """Give the session to a person and wait for it back. Their actions come back recorded;
        None when nobody answered or they aborted (the run then stops)."""
        assert self.desk is not None
        ctl = self.session.controller
        session = self.desk.session(self.run_id)
        acted_before = len(session.actions)
        self.desk.open(
            kind="takeover",
            run_id=self.run_id,
            capability=None,
            goal=self.cfg.goal,
            step_id=None,
            reason=reason,
            state_summary=(await self.surface.observe()).summary(),
            screenshot=None,
            allowed=["take_control", "resume", "abort"],
        )
        self.writer.event("discovery.handover", "system", reason=reason, detail=detail)
        ctl.escalate()
        try:
            async with asyncio.timeout(self.desk.human_timeout_s):
                state = await ctl.wait_for({ControlState.RESUME_REQUESTED, ControlState.ABORTED})
        except TimeoutError:
            for iv in self.desk.open_for(self.run_id):
                self.desk.resolve(iv.id, "timed_out", "system")
            ctl.fail("system")
            raise _Stop("needs_human", "no_human", reason) from None
        if state is ControlState.ABORTED:
            raise _Stop("needs_human", "aborted_by_operator", reason)
        ctl.start_validation()
        ctl.resume_automation()
        ctl.hand_to(LLM)
        return session.actions[acted_before:]

    async def _custom(self, block: Any) -> tuple[dict[str, Any], bool]:
        data: dict[str, Any] = dict(block.input or {})
        match block.name:
            case "fill_input":
                param = str(data.get("param"))
                if param not in self.goal.values:
                    self._record("fill_input", "custom", data, status="error", reason="unknown")
                    return _result(block, f"no declared input {param!r}", error=True), False
                action = Action(type="type", text=self.goal.values[param])
                return await self._act(block, "fill_input", [action], data, bound=param)
            case "mark_output":
                return await self._mark(block, data)
            case "declare_success":
                x, y = (int(v) for v in data.get("coordinate", [0, 0]))
                grounded = await self.surface.ground(x, y)
                self._record(
                    "declare_success",
                    "custom",
                    data,
                    status="done",
                    grounded=grounded.model_dump(mode="json"),
                )
                self.success = True
                raise _Stop("success", "declared_success")
            case "request_human":
                return await self._ask_human(block, str(data.get("reason", "")))
            case _:
                return _result(block, f"unknown tool {block.name}", error=True), False

    async def _mark(self, block: Any, data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        x, y = (int(v) for v in data.get("coordinate", [0, 0]))
        name = str(data.get("name", ""))
        grounded = await self.surface.ground(x, y)
        table = None
        if data.get("kind") == "table":
            reader = getattr(self.surface, "table_at", None)
            table = await reader(x, y) if reader is not None else None
            if table is None:
                self._record("mark_output", "custom", data, status="error", reason="no table")
                return _result(block, "There is no table at that point.", error=True), False
        self.outputs.append(name)
        money = await self._register_marked(grounded, table)
        if table is not None:
            table = {**table, "money": money}
        self._record(
            "mark_output",
            "custom",
            data,
            status="done",
            grounded=grounded.model_dump(mode="json"),
            table=table,
            money=bool(money) if table is None else None,
        )
        return _result(block, f"Marked {name}."), True

    async def _register_marked(self, grounded: Any, table: dict[str, Any] | None) -> list[str]:
        """A marked output is what the goal asked for, so it is personal: register it before the
        model can repeat it in prose. Amounts go through the redactor's amount rule, so they
        follow the policy's `redact_amounts` switch like extracted outputs do."""
        """Returns the headers of the columns whose every non-empty cell is money, so the
        compiler can type them as decimals."""
        redactor = self.writer.redactor
        if table is None:
            if not grounded.text:
                return []
            try:
                amount = money_us(grounded.text)
            except ExtractError:
                redactor.register(grounded.text, Sensitivity.pii, "marked_value")
                return []
            redactor.register(amount, Sensitivity.pii, "marked_amount")
            return ["value"]
        headers = [h for h in table["headers"] if h]
        if not table.get("caption") or not headers:
            return []
        spec = TableSpec(
            anchor_text=table["caption"],
            frame=list(grounded.frame),
            columns={f"c{i}": h for i, h in enumerate(headers)},
        )
        try:
            rows = await self.surface.extract(spec)
        except ExtractError:
            return []
        rows = rows if isinstance(rows, list) else []
        parsed = dict.fromkeys(spec.columns, 0)
        refused: set[str] = set()
        for row in rows:
            for key, cell in row.items():
                if not cell:
                    continue
                try:
                    redactor.register(money_us(cell), Sensitivity.pii, "marked_amount")
                    parsed[key] += 1
                except ExtractError:
                    refused.add(key)
        # Money: at least one amount, and every non-empty cell one. An empty column is text.
        return [spec.columns[k] for k, n in parsed.items() if n and k not in refused]

    async def _ask_human(self, block: Any, reason: str) -> tuple[dict[str, Any], bool]:
        self._record("request_human", "custom", {"reason": reason}, status="requested")
        if self.desk is None:
            raise _Stop("needs_human", "request_human", reason)
        handed = await self._hand_over("MODEL_REQUEST", reason)
        for human in handed or []:
            self._record(
                human.action.type,
                "operator",
                human.action.model_dump(exclude_none=True),
                actor=human.actor,
                status="done",
                grounded=human.grounded.model_dump(mode="json") if human.grounded else None,
                decision=_decision(human.decision),
            )
        done = self.desk.store.list(run_id=self.run_id)
        note = (done[-1].note if done else None) or "no note"
        return _result(block, f"A person handed control back. Their note: {note}"), True

    # --- the trace ----------------------------------------------------------------------------

    def _write_shot(
        self, phase: Literal["before", "after"], png: bytes, boxes: list[TextBox]
    ) -> str:
        if phase == "before":
            self._shot += 1
        self.writer.screenshot(self._shot, phase, png, text_boxes=boxes)
        return f"steps/{self._shot:04d}_{phase}.png"

    async def _quiet(self) -> None:
        """Before recording an action's after-state, let the screen settle: the fingerprint holds
        for two looks in a row, or 3 seconds pass. Authoring time only; replay never waits like
        this, it waits on predicates."""
        last = None
        deadline = time.monotonic() + QUIET_MAX_S
        while time.monotonic() < deadline:
            current = (await self.surface.observe()).fingerprint
            if current == last:
                return
            last = current
            await asyncio.sleep(QUIET_POLL_S)

    def _record(self, tool: str, kind: str, data: dict[str, Any], **fields: Any) -> None:
        self._seq += 1
        record = {
            "seq": self._seq,
            "turn": self.turns,
            "actor": fields.pop("actor", LLM),
            "tool": tool,
            "kind": kind,
            "input": data,
            "rationale": self._rationale,
            **fields,
        }
        self._rationale = None  # attached to the first action of its turn only
        self.writer.append_jsonl("trace", record)
