"""A SCRIPTED operator: a stand-in for a person at the console, for tests, the offline demo and
evidence. Every artifact it touches is labelled as scripted.

It uses only the console's HTTP API, exactly what the console page calls, and never touches the
controller, the desk or the browser directly. `follow()` is strict: it answers only the
interventions its plan expects, in order, and anything else, or a timeout, aborts and raises.
A permissive "answer everything" loop could approve something nobody reviewed."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx2 as httpx

NAME = "scripted"
LABEL = "SCRIPTED OPERATOR (stands in for a person)"


class UnexpectedIntervention(Exception):
    pass


@dataclass(frozen=True)
class Expect:
    """One intervention the plan expects, and what to do about it."""

    kind: str  # takeover | approval
    reason: str  # e.g. APPROVAL_REQUIRED, UNKNOWN_STATE
    act: Callable[["ScriptedOperator", dict[str, Any]], Awaitable[None]]


class ScriptedOperator:
    def __init__(self, base_url: str, name: str = NAME) -> None:
        self.name = name
        self.http = httpx.AsyncClient(base_url=base_url, timeout=15)

    async def close(self) -> None:
        await self.http.aclose()

    async def _post(self, url: str, **body: Any) -> dict[str, Any]:
        r = await self.http.post(url, json={"operator": self.name, **body})
        assert r.status_code == 200, f"{url}: {r.status_code} {r.text}"
        return r.json()

    async def wait_for_intervention(self, kind: str, timeout_s: float = 30) -> dict[str, Any]:
        async with asyncio.timeout(timeout_s):
            while True:
                for iv in (await self.http.get("/api/interventions", params={"open": True})).json():
                    if iv["kind"] == kind:
                        return iv
                await asyncio.sleep(0.1)

    async def wait_for_session(self, timeout_s: float = 30) -> str:
        """The run id of the first live session the console shows."""
        async with asyncio.timeout(timeout_s):
            while True:
                live = (await self.http.get("/api/sessions")).json()
                if live:
                    return live[0]["run_id"]
                await asyncio.sleep(0.05)

    async def state(self, run_id: str) -> dict[str, Any]:
        r = await self.http.get(f"/api/sessions/{run_id}")
        assert r.status_code == 200, r.text
        return r.json()

    async def wait_for_step(self, run_id: str, step: str, timeout_s: float = 30) -> None:
        async with asyncio.timeout(timeout_s):
            while True:
                r = await self.http.get(f"/api/sessions/{run_id}")
                if r.status_code == 200 and r.json()["at_step"] == step:
                    return
                await asyncio.sleep(0.05)

    async def take(self, run_id: str, *, force: bool = False) -> dict[str, Any]:
        return await self._post(f"/api/sessions/{run_id}/take", force=force)

    async def pause(self, run_id: str) -> dict[str, Any]:
        return await self._post(f"/api/sessions/{run_id}/pause")

    async def resume(self, run_id: str, note: str | None = None) -> dict[str, Any]:
        return await self._post(f"/api/sessions/{run_id}/resume", note=note)

    async def abort(self, run_id: str, note: str | None = None) -> dict[str, Any]:
        return await self._post(f"/api/sessions/{run_id}/abort", note=note)

    async def approve(self, iv_id: str, note: str | None = None) -> dict[str, Any]:
        return await self._post(f"/api/interventions/{iv_id}/approve", note=note)

    async def deny(self, iv_id: str, note: str | None = None) -> dict[str, Any]:
        return await self._post(f"/api/interventions/{iv_id}/deny", note=note)

    async def act(self, run_id: str, **action: Any) -> dict[str, Any]:
        return await self._post(f"/api/sessions/{run_id}/act", action=action)

    async def box(self, run_id: str, text: str) -> dict[str, Any]:
        """Where `text` is on screen, as the operator would see it."""
        boxes = (await self.http.get(f"/api/sessions/{run_id}/boxes")).json()
        found = [b for b in boxes if b["text"] == text]
        assert len(found) == 1, f"{text!r} is on screen {len(found)} times"
        return found[0]

    async def click_text(self, run_id: str, text: str, *, dx: int = 0) -> dict[str, Any]:
        b = await self.box(run_id, text)
        x, y = b["x"] + b["width"] // 2 + dx, b["y"] + b["height"] // 2
        if dx < 0:
            x = b["x"] + dx
        result = await self.act(run_id, type="click", x=x, y=y)
        assert result["status"] == "done", result
        return result

    async def follow(self, plan: list[Expect], timeout_s: float = 120) -> list[str]:
        """Answer exactly the planned interventions, in order. Returns what was done."""
        done: list[str] = []
        handled: set[str] = set()
        async with asyncio.timeout(timeout_s):
            while len(done) < len(plan):
                pending = [
                    iv
                    for iv in (
                        await self.http.get("/api/interventions", params={"open": True})
                    ).json()
                    if iv["id"] not in handled
                ]
                if not pending:
                    await asyncio.sleep(0.1)
                    continue
                iv = pending[0]
                step = plan[len(done)]
                if (iv["kind"], iv["reason"]) != (step.kind, step.reason):
                    await self._refuse(iv)
                    raise UnexpectedIntervention(
                        f"expected {step.kind}/{step.reason}, got {iv['kind']}/{iv['reason']}"
                    )
                handled.add(iv["id"])
                await step.act(self, iv)
                done.append(f"{iv['kind']}/{iv['reason']}")
        return done

    async def _refuse(self, iv: dict[str, Any]) -> None:
        if iv["kind"] == "approval":
            await self.deny(iv["id"], note=f"{LABEL}: not in the plan")
        else:
            await self.abort(iv["run_id"], note=f"{LABEL}: not in the plan")
