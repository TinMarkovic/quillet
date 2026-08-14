"""
Quillet MCP server — Streamable HTTP on the same process as REST.

Tools call ``api_ops`` against the Flask app's DB and email backends.
Mounted at ``/{newsletter_slug}/mcp`` on the ASGI app.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import logging
import os
import secrets
from typing import Any

from flask import Flask
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from . import api_ops

logger = logging.getLogger("quillet.mcp")

CURATED_TOOL_NAMES = (
    "list_posts",
    "get_post",
    "create_post",
    "update_post",
    "publish_post",
    "send_test",
    "send_post",
    "list_subscribers",
)


def build_mcp(app: Flask, newsletter_slug: str) -> FastMCP:
    """Build an in-process MCP server bound to one newsletter slug."""
    mcp = FastMCP(
        f"quillet-{newsletter_slug}",
        instructions=(
            f"Quillet admin tools for newsletter '{newsletter_slug}'. "
            "Same curated operations as the JSON API / OpenAPI."
        ),
        # Mounted at /{slug}/mcp — path inside the mount is "/".
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        # Basic Auth already gates access; allow local / LAN Host headers.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    def _db():
        return app.config["QUILLET_DB"]

    def _result(payload: dict[str, Any], status: int) -> str:
        return api_ops.to_json_text(payload, status)

    @mcp.tool()
    def list_posts(status: str = "all", cursor: str = "") -> str:
        """List posts for the newsletter, including drafts. Returns a page of 20.

        Pass cursor from next_cursor to fetch the next older page. Loop until next_cursor is null.

        Use the status parameter to filter:
        - all (default) — every post
        - draft — published_at is null
        - published — published_at is set (sent or unsent)
        - unsent — published but not yet full-sent (sent_at is null)
        - sent — sent_at is set

        Prefer unsent when you need a published post that has not been emailed to the list yet."""
        payload, code = api_ops.list_posts(_db(), newsletter_slug, status=status, cursor=cursor or None)
        return _result(payload, code)

    @mcp.tool()
    def get_post(post_slug: str) -> str:
        """Fetch one post by slug, including drafts. Inspect published_at and sent_at to
        determine lifecycle state (null means not published / not sent)."""
        payload, code = api_ops.get_post(_db(), newsletter_slug, post_slug)
        return _result(payload, code)

    @mcp.tool()
    def create_post(title: str, slug: str, body_md: str) -> str:
        """Create a new draft post. The post is unpublished (published_at null) and unsent
        (sent_at null) until you call publish_post / send_post."""
        payload, code = api_ops.create_post(_db(), newsletter_slug, title, slug, body_md)
        return _result(payload, code)

    @mcp.tool()
    def update_post(post_slug: str, title: str, body_md: str, slug: str = "") -> str:
        """Replace title and body for an existing post. slug is optional; omit it (or send
        empty) to keep the current slug. Publishing/sending state is unchanged by this call."""
        payload, code = api_ops.update_post(_db(), newsletter_slug, post_slug, title, body_md, slug=slug)
        return _result(payload, code)

    @mcp.tool()
    def publish_post(post_slug: str) -> str:
        """Set published_at to now. The post becomes visible on the public archive.
        Does not email subscribers — use send_test or send_post for that."""
        payload, code = api_ops.publish_post(_db(), newsletter_slug, post_slug)
        return _result(payload, code)

    @mcp.tool()
    def send_test(post_slug: str, email: str) -> str:
        """Email this post to a single address for preview. Does not mark the post as sent
        (sent_at remains null) and does not email the subscriber list.

        Prefer this over full send when you only need a personal preview."""
        payload, code = api_ops.send_test(app, newsletter_slug, post_slug, email)
        return _result(payload, code)

    @mcp.tool()
    def send_post(post_slug: str) -> str:
        """Email the post to every confirmed subscriber and set sent_at.

        Requirements:
        - Post must already be published (published_at set) — otherwise 400.
        - Post must not already have been sent (sent_at null) — otherwise 409.

        Do not use this when you only want a personal preview; use send_test instead."""
        payload, code = api_ops.send_post(app, newsletter_slug, post_slug)
        return _result(payload, code)

    @mcp.tool()
    def list_subscribers(cursor: str = "") -> str:
        """List subscribers for the newsletter (confirmed and pending), a page at a time.

        Pass cursor from next_cursor for the next older page. Inspect confirmed_at —
        null means the double opt-in is still pending."""
        payload, code = api_ops.list_subscribers(_db(), newsletter_slug, cursor=cursor or None)
        return _result(payload, code)

    return mcp


class _BasicAuthASGI:
    """ASGI middleware enforcing HTTP Basic Auth with Quillet admin credentials."""

    def __init__(self, app: ASGIApp, flask_app: Flask) -> None:
        self.app = app
        self.flask_app = flask_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        if not self._authorized(headers.get("authorization", "")):
            await Response(
                "Authentication required.",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Quillet Admin"'},
                media_type="text/plain",
            )(scope, receive, send)
            return

        await self.app(scope, receive, send)

    def _authorized(self, auth_header: str) -> bool:
        if not auth_header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(auth_header[6:].encode("ascii"), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return False
        username, _, password = decoded.partition(":")
        expected_user = self.flask_app.config.get("QUILLET_ADMIN_USERNAME", "admin")
        expected_pass = self.flask_app.config.get("QUILLET_ADMIN_PASSWORD", "")
        return secrets.compare_digest(username, expected_user) and secrets.compare_digest(password, expected_pass)


def _newsletter_slugs(flask_app: Flask) -> list[str]:
    """Newsletter slugs to mount MCP for (seed before starting the ASGI server)."""
    env_slugs = [s.strip() for s in os.environ.get("QUILLET_MCP_SLUGS", "").split(",") if s.strip()]
    if env_slugs:
        return env_slugs

    db = flask_app.config["QUILLET_DB"]
    return db.list_newsletter_slugs()


def create_asgi_app(flask_app: Flask | None = None, url_prefix: str = "") -> Starlette:
    """
    ASGI app: Flask REST/UI + MCP Streamable HTTP at ``/{slug}/mcp``.

    Seed newsletters before starting. Run with::

        uvicorn --factory quillet.factory:create_asgi_app --port 8000

    Optional: ``QUILLET_MCP_SLUGS=blog,notes`` to pin mounts without DB discovery.
    """
    from a2wsgi import WSGIMiddleware

    if flask_app is None:
        from .factory import create_app

        flask_app = create_app()

    prefix = url_prefix.rstrip("/")
    slugs = _newsletter_slugs(flask_app)
    mcp_servers: list[FastMCP] = []
    routes: list[Mount | Route] = []

    for slug in slugs:
        mcp = build_mcp(flask_app, slug)
        mcp_servers.append(mcp)
        mcp_http = mcp.streamable_http_app()
        # Only /{slug}/mcp — leave /{slug}/api/* for Flask.
        # Starlette Mount matches `/path/` ; redirect bare `/path` → `/path/`.
        mount_path = f"{prefix}/{slug}/mcp" if prefix else f"/{slug}/mcp"

        async def _redirect_to_slash(request: Request, _mount: str = mount_path) -> RedirectResponse:
            return RedirectResponse(url=f"{_mount}/", status_code=307)

        routes.append(Route(mount_path, endpoint=_redirect_to_slash, methods=["GET", "POST", "DELETE"]))
        routes.append(Mount(mount_path, app=_BasicAuthASGI(mcp_http, flask_app)))
        logger.info("MCP Streamable HTTP mounted at %s/", mount_path)

    if not slugs:
        logger.warning("No newsletter slugs for MCP mounts — REST only until restart after seed")

    routes.append(Mount("/", app=WSGIMiddleware(flask_app)))

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette):
        async with contextlib.AsyncExitStack() as stack:
            for mcp in mcp_servers:
                await stack.enter_async_context(mcp.session_manager.run())
            yield

    return Starlette(routes=routes, lifespan=lifespan)
