"""`eagleview mcp`: the catalog as an MCP server over stdio.

Each tool call runs one unattended replay and returns the RunResult as JSON. During a call,
stdout belongs to the protocol: nothing in a replay prints there (desk notices go to stderr).
Built on the MCP SDK's low-level `Server`, which takes each tool's JSON Schema as given; the
SDK's `MCPServer` (FastMCP, renamed in MCP 2) would derive schemas from Python signatures."""

from typing import Any

import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from eagleview.catalog.runner import Catalog, UnknownTool


def build_server(catalog: Catalog) -> Server[Any]:
    async def list_tools(_ctx: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(name=e.tool_name, description=e.description, input_schema=e.input_schema)
                for e in catalog.entries
            ]
        )

    async def call_tool(_ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        try:
            result = await catalog.call(params.name, params.arguments or {})
        except UnknownTool:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"unknown tool {params.name!r}")],
                is_error=True,
            )
        text = result.model_dump_json(exclude_none=True)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            is_error=result.status in ("failed", "needs_human"),
        )

    return Server(
        "eagleview",
        instructions=(
            "Deterministic tools for a credit union teller application. Each call replays a "
            "reviewed, approved capability with no LLM and returns a RunResult."
        ),
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


async def serve_stdio(catalog: Catalog) -> None:
    server = build_server(catalog)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
