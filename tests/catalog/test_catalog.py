"""The agent catalog: what it lists, the MCP server (in memory against the real bank, plus one
stdio smoke test), and the agent loop with a scripted fake model. No real model is called."""

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import httpx2
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from playwright.async_api import Browser

from eagleview.artifacts.yaml_io import load_app_profile
from eagleview.catalog.agent import ask
from eagleview.catalog.mcp_server import build_server
from eagleview.catalog.registry import anthropic_tools, approved
from eagleview.catalog.runner import Catalog
from eagleview.discovery.client import make_client
from eagleview.discovery.meter import MemoryMeter
from eagleview.safety.redaction import Redactor
from tests.discovery.fake_model import custom, message
from tests.fixtures import APP_PROFILE, OPEN_SHARE, REPO
from tests.support.bank import Bank
from tests.support.replay import approved_copy, policy_for, tenant_for

LOOKUP = REPO / "capabilities" / "drafts" / "member.lookup_share_balances.yaml"
TOOL = "member_lookup_share_balances"


@pytest.fixture
def catalog_dir(tmp_path: Path) -> Path:
    """An approved copy of the discovered lookup (test-only; approving is the user's step), a
    verified draft, and an approved irreversible capability."""
    root = tmp_path / "capabilities"
    root.mkdir()
    shutil.copy(approved_copy(LOOKUP, tmp_path), root / "member.lookup_share_balances.yaml")
    shutil.copy(LOOKUP, root / "verified_only.yaml")
    shutil.copy(approved_copy(OPEN_SHARE, tmp_path), root / "member.open_share.yaml")
    return root


def test_only_approved_reversible_capabilities_are_tools(catalog_dir: Path) -> None:
    [entry] = approved(catalog_dir)
    assert entry.tool_name == TOOL
    [tool] = anthropic_tools([entry])
    schema = tool["input_schema"]
    assert schema["properties"]["member_id"]["pattern"] == "^[0-9]{4,10}$"
    assert schema["required"] == ["member_id"] and schema["additionalProperties"] is False
    assert "MEMBER_NOT_FOUND" in tool["description"]
    assert "not errors" in tool["description"] and "Risk: read_only" in tool["description"]


def catalog_for(bank: Bank, catalog_dir: Path, browser: Browser, tmp_path: Path) -> Catalog:
    return Catalog(
        approved(catalog_dir),
        profile=load_app_profile(APP_PROFILE),
        tenant=tenant_for(bank),
        policy=policy_for(bank),
        runs_root=tmp_path / "runs",
        environ={bank.tenant.password_env: bank.password},
        browser=browser,
    )


async def test_mcp_tools_list_and_call_against_the_bank(
    browser: Browser, lantern: Bank, catalog_dir: Path, tmp_path: Path
) -> None:
    server = build_server(catalog_for(lantern, catalog_dir, browser, tmp_path))
    async with Client(server) as client:
        tools = (await client.list_tools()).tools
        assert [t.name for t in tools] == [TOOL]
        assert tools[0].input_schema["properties"]["member_id"]["pattern"]
        found = await client.call_tool(TOOL, {"member_id": "10482"})
        result = json.loads(found.content[0].text)  # type: ignore[union-attr]
        assert not found.is_error and result["status"] == "success"
        assert result["outputs"]["share_balances"][0]["balance"].count(".") == 1
        missing = await client.call_tool(TOOL, {"member_id": "99999"})
        assert json.loads(missing.content[0].text)["outcome"]["code"] == "MEMBER_NOT_FOUND"  # type: ignore[union-attr]
        bad = await client.call_tool(TOOL, {"member_id": "10 482"})
        assert json.loads(bad.content[0].text)["status"] == "rejected"  # type: ignore[union-attr]
        unknown = await client.call_tool("no_such_tool", {})
        assert unknown.is_error


async def test_mcp_over_stdio_keeps_stdout_for_the_protocol(catalog_dir: Path) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-c",
            "from eagleview.cli import app; app()",
            "mcp",
            "--capabilities-dir",
            str(catalog_dir),
        ],
        cwd=str(REPO),
    )
    async with Client(params) as client:
        tools = (await client.list_tools()).tools
    assert [t.name for t in tools] == [TOOL]


async def test_the_agent_picks_the_tool_and_answers_from_the_result(
    browser: Browser, lantern: Bank, catalog_dir: Path, tmp_path: Path
) -> None:
    bodies: list[dict[str, Any]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return httpx2.Response(200, json=message([custom(TOOL, member_id="10482")]))
        result = json.loads(body["messages"][-1]["content"][0]["content"])
        balance = result["outputs"]["share_balances"][0]["balance"]
        text = f"Member 10482's first share holds {balance}."
        return httpx2.Response(200, json=message([{"type": "text", "text": text}], "end_turn"))

    meter = MemoryMeter()
    answer = await ask(
        "What's in member 10482's savings?",
        client=make_client(
            {"ANTHROPIC_API_KEY": "sk-fake"},
            Redactor(),
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
        ),
        model="claude-sonnet-5-5",
        meter=meter,
        catalog=catalog_for(lantern, catalog_dir, browser, tmp_path),
    )
    assert answer.tool_calls[0]["status"] == "success"
    assert "first share holds" in answer.answer
    assert bodies[0]["tool_choice"] == {"type": "auto"}
    assert [t["name"] for t in bodies[0]["tools"]] == [TOOL]
    assert len(meter.entries) == 2  # every model call is priced
