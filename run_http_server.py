#!/usr/bin/env python3
"""
Run platform-docs MCP server with HTTP transport for local testing.

This script starts the MCP server on http://localhost:8000/mcp/
for local HTTP validation.

Usage:
    uv run python run_http_server.py

The server will be accessible at:
    http://localhost:8000/mcp/
"""
from src.platform_docs.server import mcp

if __name__ == "__main__":
    # Run with HTTP transport for local testing
    mcp.run(transport="sse")
