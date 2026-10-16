"""A scripted stand-in for Claude, served through the real SDK client by an httpx2 transport.

Each scripted turn is a function of the live screen: it locates its targets with Playwright on
the session's page (a test-only shortcut for what the real model reads off the screenshot), then
returns tool calls in the computer toolset's exact shape. Every request body is kept, so tests
can check what the loop sent back."""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx2

from eagleview.control.interventions import InterventionDesk

MODEL = "claude-sonnet-5-5"
Turn = Callable[[Any], Awaitable[list[dict[str, Any]]]]


def message(content: list[dict[str, Any]], stop_reason: str = "tool_use") -> dict[str, Any]:
    return {
        "id": "msg_fake",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 500},
    }


_ids = iter(range(1, 1_000_000))


def member(name: str, /, **inp: Any) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": f"toolu_{next(_ids)}",
        "name": name,
        "input": inp,
        "toolset_name": "computer",
    }


def custom(tool: str, /, **inp: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": f"toolu_{next(_ids)}", "name": tool, "input": inp}


def thinking(text: str) -> dict[str, Any]:
    return {"type": "thinking", "thinking": text, "signature": "sig"}


@dataclass
class FakeModel:
    desk: InterventionDesk
    inputs: list[dict[str, str]]
    turns: list[Turn]
    requests: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    errors: list[BaseException] = field(default_factory=list[BaseException])

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self._handle))

    @property
    def surface(self) -> Any:
        (session,) = self.desk.sessions.values()
        return session.surface

    async def _handle(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        if "format" in body.get("output_config", {}):
            text = json.dumps({"inputs": self.inputs})
            return httpx2.Response(200, json=message([{"type": "text", "text": text}], "end_turn"))
        turn = len(self.requests) - 2  # the first request extracted the inputs
        if turn >= len(self.turns):
            return httpx2.Response(
                200, json=message([{"type": "text", "text": "I am done."}], "end_turn")
            )
        try:
            content = await self.turns[turn](self.surface)
        except Exception as exc:  # surfaced by the test, not swallowed by SDK retries
            self.errors.append(exc)
            raise
        return httpx2.Response(200, json=message(content))


async def centre(surface: Any, frame: str, selector: str) -> list[int]:
    """Where an element is on screen, in screenshot pixels."""
    loc = surface.page.frame_locator(f"frame[name='{frame}']").locator(selector)
    box = await loc.bounding_box()
    assert box is not None, selector
    return [int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2)]
