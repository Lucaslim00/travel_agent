"""
Shared MCP JSON-RPC protocol helpers.

Every MCP server imports from here instead of duplicating the boilerplate.

Usage in a server module:
    from mcp_server.mcp_protocol import run_server
    run_server("weather-api", "1.0.0", TOOLS, TOOL_HANDLERS)
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable


def send_response(id: Any, result: dict) -> None:
    response = {"jsonrpc": "2.0", "id": id, "result": result}
    msg = json.dumps(response)
    sys.stdout.write(f"Content-Length: {len(msg)}\r\n\r\n{msg}")
    sys.stdout.flush()


def send_error(id: Any, code: int, message: str) -> None:
    response = {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}
    msg = json.dumps(response)
    sys.stdout.write(f"Content-Length: {len(msg)}\r\n\r\n{msg}")
    sys.stdout.flush()


def read_message() -> dict | None:
    """Read a JSON-RPC message from stdin (Content-Length framing)."""
    header = ""
    while True:
        line = sys.stdin.readline()
        if not line:
            return None
        header += line
        if header.endswith("\r\n\r\n") or header.endswith("\n\n"):
            break

    content_length = 0
    for h in header.strip().split("\n"):
        if h.strip().lower().startswith("content-length:"):
            content_length = int(h.split(":")[1].strip())

    if content_length == 0:
        return None

    body = sys.stdin.read(content_length)
    return json.loads(body)


def run_server(
    server_name: str,
    version: str,
    tools: list[dict],
    tool_handlers: dict[str, Callable],
) -> None:
    """
    Run a generic MCP server loop.

    Args:
        server_name: Name reported during initialize (e.g. "weather-api")
        version: Server version string
        tools: List of MCP tool definition dicts
        tool_handlers: Map of tool_name -> handler function(params) -> str
    """
    while True:
        request = read_message()
        if request is None:
            break

        method = request.get("method", "")
        id = request.get("id")
        params = request.get("params", {})

        if method == "initialize":
            send_response(id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": server_name, "version": version},
            })

        elif method == "notifications/initialized":
            pass

        elif method == "tools/list":
            send_response(id, {"tools": tools})

        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_input = params.get("arguments", {})
            handler = tool_handlers.get(tool_name)
            if not handler:
                send_error(id, -32602, f"Unknown tool: {tool_name}")
                continue
            try:
                result_text = handler(tool_input)
                send_response(id, {
                    "content": [{"type": "text", "text": result_text}],
                    "isError": False,
                })
            except Exception as e:
                send_response(id, {
                    "content": [{"type": "text", "text": f"Error: {str(e)}"}],
                    "isError": True,
                })

        elif method == "ping":
            send_response(id, {})

        else:
            if id is not None:
                send_error(id, -32601, f"Method not found: {method}")
