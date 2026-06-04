#!/usr/bin/env python3
"""Listener MCP server (ADR-020) — exposes the dashboard tools over MCP.

Streamable-HTTP transport on 127.0.0.1:8765 (endpoint /mcp). The dashboard
launches + restarts this from the Settings page; it can also be pointed at by
any MCP client (Claude Desktop, etc.). Tools are defined in assistant_tools.py.

    python mcp_server.py
"""
import assistant_tools
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("listener", host="127.0.0.1", port=8765)

for _fn in assistant_tools.TOOLS:
    mcp.tool()(_fn)

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
