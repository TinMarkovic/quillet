#!/usr/bin/env python3
"""
run_mcp_server.py — stdio launcher for the Quillet MCP server.

Environment variables:
    QUILLET_MCP_BASE_URL         Quillet instance base URL (required)
    QUILLET_MCP_NEWSLETTER_SLUG  Newsletter slug (default: spike)
    QUILLET_MCP_USERNAME         Basic Auth username (default: admin)
    QUILLET_MCP_PASSWORD         Basic Auth password (required)

Example:
    QUILLET_MCP_BASE_URL=http://127.0.0.1:8000 \
    QUILLET_MCP_PASSWORD=secret \
    python scripts/run_mcp_server.py
"""

import os
import sys


def main() -> int:
    base_url = os.environ.get("QUILLET_MCP_BASE_URL", "").rstrip("/")
    password = os.environ.get("QUILLET_MCP_PASSWORD", "")
    newsletter_slug = os.environ.get("QUILLET_MCP_NEWSLETTER_SLUG", "spike")
    username = os.environ.get("QUILLET_MCP_USERNAME", "admin")

    if not base_url:
        print("QUILLET_MCP_BASE_URL is required.", file=sys.stderr)
        return 1
    if not password:
        print("QUILLET_MCP_PASSWORD is required.", file=sys.stderr)
        return 1

    from quillet.mcp_server import build_server

    server = build_server(
        base_url=base_url,
        newsletter_slug=newsletter_slug,
        username=username,
        password=password,
    )
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
