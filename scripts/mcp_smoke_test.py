#!/usr/bin/env python3
"""
mcp_smoke_test.py — MCP tool-list + create_post round-trip against an in-process Quillet.

Run from the repo root with the virtualenv active:
    python scripts/mcp_smoke_test.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from wsgiref.simple_server import make_server

from flask import Flask

from quillet import create_blueprint
from quillet.db.sqlalchemy import SQLAlchemyRepository
from quillet.email.noop import NoopSender
from quillet.mcp_server import CURATED_TOOL_NAMES, build_server


def _check(label: str, ok: bool) -> bool:
    print(f"  [{'OK' if ok else 'FAIL'}] {label}")
    return ok


def run() -> int:
    # File-backed SQLite so the WSGI thread and MCP HTTP client share one DB.
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "mcp_smoke.db")
        db = SQLAlchemyRepository(f"sqlite:///{db_path}")
        db.create_newsletter(
            slug="spike",
            name="Spike Fixture",
            from_email="spike@example.com",
            from_name="Spike",
            reply_to="owner@example.com",
        )

        app = Flask(__name__)
        app.register_blueprint(
            create_blueprint(
                db=db,
                email=NoopSender(),
                admin_password="secret",
                mode="api",
            )
        )

        server = make_server("127.0.0.1", 0, app)
        port = server.server_port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        time.sleep(0.1)

        base_url = f"http://127.0.0.1:{port}"
        mcp = build_server(
            base_url=base_url,
            newsletter_slug="spike",
            username="admin",
            password="secret",
        )

        failures = 0

        print("\n-- MCP tool list --")
        tools = mcp._tool_manager.list_tools()
        names = sorted(t.name for t in tools)
        expected = sorted(CURATED_TOOL_NAMES)
        failures += not _check(f"curated set ({len(expected)} tools)", names == expected)
        if names != expected:
            print(f"    got:      {names}")
            print(f"    expected: {expected}")

        print("\n-- MCP create_post round-trip --")
        create_tool = mcp._tool_manager.get_tool("create_post")
        get_tool = mcp._tool_manager.get_tool("get_post")
        if create_tool is None or get_tool is None:
            failures += not _check("create_post/get_post tools present", False)
        else:
            created_raw = create_tool.fn(
                title="MCP Smoke",
                slug="mcp-smoke",
                body_md="# MCP Smoke\n\nCreated via MCP tool.",
            )
            created = json.loads(created_raw)
            ok_create = "post" in created and created["post"].get("slug") == "mcp-smoke"
            failures += not _check("create_post returns post", ok_create)

            fetched_raw = get_tool.fn(post_slug="mcp-smoke")
            fetched = json.loads(fetched_raw)
            ok_get = (
                "post" in fetched
                and fetched["post"].get("slug") == "mcp-smoke"
                and fetched["post"].get("published_at") is None
            )
            failures += not _check("get_post sees draft", ok_get)

        server.shutdown()
        print(f"\n{'All checks passed.' if not failures else f'{failures} check(s) FAILED.'}")
        return failures


if __name__ == "__main__":
    sys.exit(run())
