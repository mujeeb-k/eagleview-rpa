"""An AI agent using the catalog: Claude, with plain tool use (no computer use), picks a tool,
the capability replays deterministically, and Claude answers from the RunResult.

Every model call is priced into the same ledger as discovery, so the spend limit covers it."""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from secrets import token_hex
from typing import Any

import anthropic

from eagleview.catalog.registry import anthropic_tools
from eagleview.catalog.runner import Catalog
from eagleview.discovery.meter import Meter

SYSTEM = """You help a credit union teller answer questions about members. Use the tools to \
look things up; never guess a value. A tool returns a RunResult: status "success" carries \
typed outputs, and status "outcome" is a legitimate business answer (for example, no such \
member), not an error. Answer in one or two plain sentences."""

MAX_TURNS = 5


@dataclass
class AgentAnswer:
    run_id: str
    answer: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])


async def ask(
    question: str,
    *,
    client: anthropic.AsyncAnthropic,
    model: str,
    meter: Meter,
    catalog: Catalog,
) -> AgentAnswer:
    run_id = f"r_agent_{datetime.now(UTC):%Y%m%dT%H%M%S}_{token_hex(3)}"
    tools = anthropic_tools(catalog.entries)
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    out = AgentAnswer(run_id, "")
    for _ in range(MAX_TURNS):
        meter.before_request(run_id)
        response = await client.messages.create(
            model=model,
            max_tokens=4000,
            system=SYSTEM,
            tools=tools,  # type: ignore[arg-type]
            tool_choice={"type": "auto"},  # forced tool use is refused on this model family
            output_config={"effort": "low"},
            messages=messages,  # type: ignore[arg-type]
        )
        meter.after_response(run_id, model, response, "agent")
        messages.append({"role": "assistant", "content": response.content})
        uses = [b for b in response.content if b.type == "tool_use"]
        if not uses:
            out.answer = " ".join(b.text for b in response.content if b.type == "text").strip()
            return out
        results: list[dict[str, Any]] = []
        for use in uses:
            result = await catalog.call(use.name, dict(use.input or {}))
            out.tool_calls.append(
                {"tool": use.name, "status": result.status, "run_id": result.run_id}
            )
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": use.id,
                    "content": result.model_dump_json(exclude_none=True),
                }
            )
        messages.append({"role": "user", "content": results})
    out.answer = "(no answer within the turn limit)"
    return out


def describe(answer: AgentAnswer) -> str:
    calls = json.dumps(answer.tool_calls)
    return f"{answer.answer}\n  tool calls: {calls}"
