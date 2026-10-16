"""Interventions: requests for a human, kept in SQLite, plus the desk that ties a live run to the
operators answering it.

The store is a file under `runs/` (gitignored). The desk lives in the runner's process. It holds
each live session (surface, controller, writer), opens and resolves interventions, wakes the engine
waiting on one, and relays operator input through `Surface.act` at the controller's current
epoch. Nothing here touches the browser any other way."""

import asyncio
import json
import re
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex
from typing import Literal

from eagleview.control.session_controller import SessionController
from eagleview.schema.action import OBSERVE_ACTIONS, Action
from eagleview.schema.intervention import Intervention, OperatorAction, Resolution
from eagleview.schema.surface import GroundedTarget
from eagleview.surfaces.base import ActionResult, Surface

_OPERATOR = re.compile(r"^[a-z0-9_.-]{1,64}$")
DEFAULT_HUMAN_TIMEOUT_S = 900.0


def operator_actor(name: str) -> str:
    """`ana` -> `operator:ana`. Names are checked here, at the boundary, against the Actor rule."""
    if not _OPERATOR.fullmatch(name):
        raise ValueError(f"operator names are 1-64 of a-z 0-9 _ . - (got {name!r})")
    return f"operator:{name}"


class InterventionStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS interventions ("
            " id TEXT PRIMARY KEY, run_id TEXT NOT NULL, created_at TEXT NOT NULL,"
            " resolution TEXT, data TEXT NOT NULL)"
        )
        self._db.commit()

    def add(self, iv: Intervention) -> Intervention:
        self._db.execute(
            "INSERT INTO interventions (id, run_id, created_at, resolution, data)"
            " VALUES (?, ?, ?, ?, ?)",
            (iv.id, iv.run_id, iv.created_at.isoformat(), iv.resolution, iv.model_dump_json()),
        )
        self._db.commit()
        return iv

    def get(self, iv_id: str) -> Intervention | None:
        row = self._db.execute("SELECT data FROM interventions WHERE id = ?", (iv_id,)).fetchone()
        return None if row is None else Intervention.model_validate(json.loads(row[0]))

    def list(self, *, open_only: bool = False, run_id: str | None = None) -> list[Intervention]:
        sql = "SELECT data FROM interventions"
        where: list[str] = []
        args: list[str] = []
        if open_only:
            where.append("resolution IS NULL")
        if run_id is not None:
            where.append("run_id = ?")
            args.append(run_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at, id"
        return [Intervention.model_validate(json.loads(r[0])) for r in self._db.execute(sql, args)]

    def resolve(
        self, iv_id: str, resolution: Resolution, by: str, note: str | None = None
    ) -> Intervention:
        iv = self.get(iv_id)
        if iv is None:
            raise KeyError(iv_id)
        if iv.resolution is not None:
            raise ValueError(f"{iv_id} is already {iv.resolution}")
        done = iv.model_copy(
            update={
                "resolution": resolution,
                "resolved_by": by,
                "resolved_at": datetime.now(UTC),
                "note": note,
            }
        )
        self._db.execute(
            "UPDATE interventions SET resolution = ?, data = ? WHERE id = ?",
            (resolution, done.model_dump_json(), iv_id),
        )
        self._db.commit()
        return done

    def close(self) -> None:
        self._db.close()


@dataclass(frozen=True)
class HumanAction:
    """One operator action that changed something, with what grounding found under it."""

    action: Action
    actor: str
    grounded: GroundedTarget | None


@dataclass
class LiveSession:
    run_id: str
    capability: str
    surface: Surface
    controller: SessionController
    at_step: str | None = None
    actions: list[HumanAction] = field(default_factory=list[HumanAction])
    # An operator's irreversible action went through: the run counts as committed.
    committed: bool = False


class InterventionDesk:
    def __init__(
        self,
        store: InterventionStore,
        *,
        console_url: str | None = None,
        human_timeout_s: float = DEFAULT_HUMAN_TIMEOUT_S,
        notify: Callable[[str], None] | None = None,
    ) -> None:
        self.store = store
        self.console_url = console_url
        self.human_timeout_s = human_timeout_s
        # A line on stderr: stdout carries the result JSON.
        self.notify = notify or (lambda line: print(line, file=sys.stderr, flush=True))
        self.sessions: dict[str, LiveSession] = {}
        self._waiters: dict[str, asyncio.Event] = {}

    # --- sessions -----------------------------------------------------------------------------

    def attach(self, session: LiveSession) -> None:
        self.sessions[session.run_id] = session

    def detach(self, run_id: str) -> None:
        self.sessions.pop(run_id, None)

    def session(self, run_id: str) -> LiveSession:
        try:
            return self.sessions[run_id]
        except KeyError:
            raise KeyError(f"no live session {run_id!r}") from None

    # --- interventions ------------------------------------------------------------------------

    def open(
        self,
        *,
        kind: Literal["takeover", "approval"],
        run_id: str,
        capability: str,
        step_id: str | None,
        reason: str,
        state_summary: str,
        screenshot: str | None,
        allowed: list[OperatorAction],
    ) -> Intervention:
        iv = Intervention(
            id=f"iv_{datetime.now(UTC):%Y%m%dT%H%M%S}_{token_hex(3)}",
            kind=kind,
            run_id=run_id,
            capability=capability,
            step_id=step_id,
            reason=reason,
            state_summary=state_summary,
            screenshot=screenshot,
            allowed_actions=allowed,
            created_at=datetime.now(UTC),
        )
        self.store.add(iv)
        self._waiters[iv.id] = asyncio.Event()
        if self.notify is not None:
            where = f" at {self.console_url}/sessions/{run_id}" if self.console_url else ""
            self.notify(f"[eagleview] {kind} needed for {run_id} ({reason}){where}")
        return iv

    def resolve(
        self, iv_id: str, resolution: Resolution, by: str, note: str | None = None
    ) -> Intervention:
        done = self.store.resolve(iv_id, resolution, by, note)
        event = self._waiters.pop(iv_id, None)
        if event is not None:
            event.set()
        return done

    def open_for(self, run_id: str, kind: str | None = None) -> list[Intervention]:
        return [
            iv
            for iv in self.store.list(open_only=True, run_id=run_id)
            if kind is None or iv.kind == kind
        ]

    async def wait(self, iv_id: str, timeout_s: float | None = None) -> Intervention:
        """Until the intervention is resolved, or `timeout_s` passes (then: timed_out)."""
        event = self._waiters.get(iv_id)
        if event is not None:
            try:
                async with asyncio.timeout(timeout_s or self.human_timeout_s):
                    await event.wait()
            except TimeoutError:
                return self.resolve(iv_id, "timed_out", "system")
        iv = self.store.get(iv_id)
        assert iv is not None
        return iv

    # --- operator input -----------------------------------------------------------------------

    async def operator_act(self, run_id: str, operator: str, action: Action) -> ActionResult:
        """Relay one operator action through `Surface.act`, as `operator:<name>` at the current
        epoch. Grounding runs on it like on any other actor's, and is recorded."""
        actor = operator_actor(operator)
        s = self.session(run_id)
        s.surface.record_candidates = True
        try:
            result = await s.surface.act(action, actor, s.controller.epoch)
        finally:
            s.surface.record_candidates = False
        if result.ok and action.type not in OBSERVE_ACTIONS:
            s.actions.append(HumanAction(action, actor, result.grounded))
            if result.decision is not None and result.decision.irreversible:
                s.committed = True
        return result
