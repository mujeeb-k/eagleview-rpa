"""Spike C: confirm the installed anthropic SDK handles the computer toolset end to end.

The offline test drives two turns of a discovery-style loop through the real SDK client with
an ``httpx2.MockTransport`` (anthropic 1.x speaks ``httpx2``, so an ``httpx`` transport would
never see the traffic). It checks the request shape we send, that ``toolset_name`` survives
parsing, and that it survives the round trip when ``response.content`` is appended back as the
assistant turn.

Note: request-side ``tools`` are TypedDicts and are not validated by the SDK at runtime, so the
offline test proves serialization only. Server acceptance is covered by the ``live`` test.
"""

import json
import os
from typing import Any

import anthropic
import httpx2
import pytest
from anthropic.types import MessageParam, ToolResultBlockParam, ToolUseBlock

MODEL = "claude-sonnet-5-5"
TOOLSET = {"type": "computer_toolset_20260801"}
HALT_TEXT = "Not executed: an earlier computer action in this turn failed."
# 1x1 transparent PNG: stands in for a screenshot; image bytes are irrelevant to the spike.
PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kg"
    "AAAABJRU5ErkJggg=="
)


def _message(content: list[dict[str, Any]], stop_reason: str) -> dict[str, Any]:
    return {
        "id": "msg_spike",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


TURN_1 = _message(
    [
        {"type": "thinking", "thinking": "Open Member Inquiry, then search.", "signature": "sig"},
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "left_click",
            "input": {"coordinate": [84, 212]},
            "toolset_name": "computer",
        },
        {
            "type": "tool_use",
            "id": "toolu_2",
            "name": "type",
            "input": {"text": "10482"},
            "toolset_name": "computer",
        },
        {
            "type": "tool_use",
            "id": "toolu_3",
            "name": "screenshot",
            "input": {},
            "toolset_name": "computer",
        },
    ],
    "tool_use",
)
TURN_2 = _message([{"type": "text", "text": "Done."}], "end_turn")


def test_sdk_round_trips_computer_toolset_offline() -> None:
    sent: list[httpx2.Request] = []
    replies = iter([TURN_1, TURN_2])

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(200, json=next(replies))

    client = anthropic.Anthropic(
        api_key="sk-ant-offline-spike",  # never sent anywhere: the transport is a mock
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
        max_retries=0,
    )
    messages: list[MessageParam] = [
        {"role": "user", "content": "Look up member 10482 and read their share balances."}
    ]
    first = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        thinking={"type": "adaptive", "display": "summarized"},
        tools=[{"type": "computer_toolset_20260801"}],
        messages=messages,
    )

    # Response side: member calls parse as plain ToolUseBlock with a typed toolset_name field.
    calls = [b for b in first.content if b.type == "tool_use"]
    assert all(isinstance(b, ToolUseBlock) for b in calls)
    assert [(b.name, b.toolset_name) for b in calls] == [
        ("left_click", "computer"),
        ("type", "computer"),
        ("screenshot", "computer"),
    ]
    assert first.content[0].type == "thinking"

    # Batch semantics from CLAUDE.md: the second block fails, so the third is not executed.
    results: list[ToolResultBlockParam] = [
        {
            "type": "tool_result",
            "tool_use_id": "toolu_1",
            "content": "OK",
            "toolset_name": "computer",
        },
        {
            "type": "tool_result",
            "tool_use_id": "toolu_2",
            "content": "Blocked by policy.",
            "is_error": True,
            "toolset_name": "computer",
        },
        {
            "type": "tool_result",
            "tool_use_id": "toolu_3",
            "content": HALT_TEXT,
            "is_error": True,
            "toolset_name": "computer",
        },
    ]
    messages.append({"role": "assistant", "content": first.content})
    messages.append(
        {
            "role": "user",
            "content": [
                *results,
                {"type": "text", "text": "Fresh screenshot after the batch:"},
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": PNG_1PX},
                },
            ],
        }
    )
    second = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        thinking={"type": "adaptive", "display": "summarized"},
        tools=[{"type": "computer_toolset_20260801"}],
        messages=messages,
    )
    assert second.stop_reason == "end_turn"

    # Request side: the toolset entry goes out bare, with no beta header.
    assert len(sent) == 2
    body_1 = json.loads(sent[0].content)
    assert body_1["tools"] == [TOOLSET]
    assert body_1["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert "anthropic-beta" not in sent[0].headers

    # The assistant turn we echo back keeps toolset_name on every member call, and every
    # tool_result carries it too (the API rejects a member result without it).
    body_2 = json.loads(sent[1].content)
    echoed = body_2["messages"][1]["content"]
    assert [b.get("toolset_name") for b in echoed if b["type"] == "tool_use"] == ["computer"] * 3
    sent_results = [b for b in body_2["messages"][2]["content"] if b["type"] == "tool_result"]
    assert [b["toolset_name"] for b in sent_results] == ["computer"] * 3
    assert sent_results[2] == {
        "type": "tool_result",
        "tool_use_id": "toolu_3",
        "content": HALT_TEXT,
        "is_error": True,
        "toolset_name": "computer",
    }


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
def test_api_accepts_computer_toolset_live() -> None:
    # Key from the environment; never printed or logged. An org-level key that is not scoped to a
    # workspace must also name one, and the first-party client only takes it as a header.
    workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    client = anthropic.Anthropic(
        default_headers={"anthropic-workspace-id": workspace} if workspace else None
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        thinking={"type": "adaptive", "display": "summarized"},
        output_config={"effort": "low"},
        tools=[{"type": "computer_toolset_20260801"}],
        messages=[
            {
                "role": "user",
                "content": "Take one screenshot so you can see the screen. Do nothing else.",
            }
        ],
    )
    assert response.stop_reason == "tool_use", f"stop_reason={response.stop_reason}"
    calls = [b for b in response.content if b.type == "tool_use"]
    assert calls, "expected at least one tool_use block"
    assert calls[0].name == "screenshot"
    assert all(b.toolset_name == "computer" for b in calls)
