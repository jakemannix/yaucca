"""Allow running the MCP server with `python -m yaucca`."""

from yaucca.mcp_server import mcp

mcp.run(transport="stdio")
