#!/usr/bin/env python3
"""
mcp_smoke_test.py — MCP Streamable HTTP on the Quillet ASGI app.

Verifies tools/list + create_post against /{slug}/mcp/.

Run from the repo root with the virtualenv active:
    python scripts/mcp_smoke_test.py
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile

from flask import Flask
from starlette.testclient import TestClient

from quillet import create_blueprint
from quillet.db.sqlalchemy import SQLAlchemyRepository
from quillet.email.noop import NoopSender
from quillet.mcp_server import CURATED_TOOL_NAMES, create_asgi_app


def _check(label: str, ok: bool) -> bool:
    print(f"  [{'OK' if ok else 'FAIL'}] {label}")
    return ok


def _auth_header(password: str = "secret") -> str:
    return "Basic " + base64.b64encode(f"admin:{password}".encode()).decode()


def run() -> int:
    failures = 0

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "mcp_smoke.db")
        db = SQLAlchemyRepository(f"sqlite:///{db_path}")
        db.create_newsletter(
            slug="blog",
            name="Test Blog",
            from_email="hi@example.com",
            from_name="Author",
            reply_to=None,
        )

        flask_app = Flask(__name__)
        flask_app.register_blueprint(
            create_blueprint(
                db=db,
                email=NoopSender(),
                admin_password="secret",
                mode="api",
            )
        )
        asgi = create_asgi_app(flask_app)

        headers = {
            "Authorization": _auth_header(),
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }

        with TestClient(asgi) as client:
            print("\n-- MCP bootstrap doc --")
            r = client.get("/blog/api/mcp.md")
            failures += not _check("mcp.md 200", r.status_code == 200)
            failures += not _check(
                "mcp.md is URL-based",
                '"url"' in r.text and "/mcp/" in r.text and "python -m quillet.mcp_server" not in r.text,
            )

            print("\n-- Auth --")
            r = client.post("/blog/mcp/", json={"jsonrpc": "2.0", "id": 0, "method": "initialize"})
            failures += not _check("initialize without auth → 401", r.status_code == 401)

            print("\n-- MCP initialize + tools/list --")
            r = client.post(
                "/blog/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "mcp_smoke", "version": "0"},
                    },
                },
                headers=headers,
                follow_redirects=True,
            )
            failures += not _check("initialize 200", r.status_code == 200)

            session = r.headers.get("mcp-session-id")
            call_headers = dict(headers)
            if session:
                call_headers["Mcp-Session-Id"] = session

            client.post(
                "/blog/mcp/",
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=call_headers,
            )

            r = client.post(
                "/blog/mcp/",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                headers=call_headers,
            )
            failures += not _check("tools/list 200", r.status_code == 200)
            tools = r.json().get("result", {}).get("tools", [])
            names = sorted(t["name"] for t in tools)
            failures += not _check(
                f"curated set ({len(CURATED_TOOL_NAMES)} tools)",
                names == sorted(CURATED_TOOL_NAMES),
            )
            if names != sorted(CURATED_TOOL_NAMES):
                print(f"    got:      {names}")
                print(f"    expected: {sorted(CURATED_TOOL_NAMES)}")

            print("\n-- MCP create_post round-trip --")
            r = client.post(
                "/blog/mcp/",
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "create_post",
                        "arguments": {
                            "title": "MCP Smoke",
                            "slug": "mcp-smoke",
                            "body_md": "# MCP Smoke\n\nCreated via HTTP MCP.",
                        },
                    },
                },
                headers=call_headers,
            )
            failures += not _check("create_post tool 200", r.status_code == 200)
            text = r.json().get("result", {}).get("content", [{}])[0].get("text", "")
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = {}
            failures += not _check(
                "create_post returns draft",
                payload.get("status") == 201 and payload.get("post", {}).get("slug") == "mcp-smoke",
            )

            r = client.get("/blog/api/posts/mcp-smoke", headers={"Authorization": _auth_header()})
            failures += not _check(
                "REST sees MCP-created post",
                r.status_code == 200 and r.json().get("post", {}).get("slug") == "mcp-smoke",
            )

    print(f"\n{'All checks passed.' if not failures else f'{failures} check(s) FAILED.'}")
    return failures


if __name__ == "__main__":
    sys.exit(run())
