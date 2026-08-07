"""
Quillet MCP server — thin wrapper over the curated admin JSON API.

Tool descriptions are mirrored from quillet/openapi.yaml so both spike arms
receive the same editorial curation. Business logic stays in the HTTP API;
this module only translates MCP tool calls into authenticated requests.
"""

from __future__ import annotations

import json
from typing import Any

import requests
from mcp.server.fastmcp import FastMCP


def build_server(
    base_url: str,
    newsletter_slug: str,
    username: str,
    password: str,
) -> FastMCP:
    """
    Build an MCP server whose tools call the Quillet JSON API over HTTP.

    Args:
        base_url: Quillet instance base URL (no trailing slash), including any
            blueprint url_prefix (e.g. ``http://127.0.0.1:8000``).
        newsletter_slug: Newsletter slug the tools operate on (e.g. ``spike``).
        username: Basic Auth username.
        password: Basic Auth password.
    """
    mcp = FastMCP("quillet")
    root = base_url.rstrip("/")
    session = requests.Session()
    session.auth = (username, password)
    session.headers.update({"Accept": "application/json"})

    def _url(*parts: str) -> str:
        return "/".join([root, newsletter_slug, "api", *parts])

    def _response_text(response: requests.Response) -> str:
        try:
            payload: Any = response.json()
            return json.dumps(payload, default=str)
        except ValueError:
            return response.text

    @mcp.tool()
    def list_posts(status: str = "all") -> str:
        """List posts for the newsletter, including drafts.

        Use the status parameter to filter:
        - all (default) — every post
        - draft — published_at is null
        - published — published_at is set (sent or unsent)
        - unsent — published but not yet full-sent (sent_at is null)
        - sent — sent_at is set

        Prefer unsent when you need a published post that has not been emailed to the list yet."""
        response = session.get(_url("posts"), params={"status": status})
        return _response_text(response)

    @mcp.tool()
    def get_post(post_slug: str) -> str:
        """Fetch one post by slug, including drafts. Inspect published_at and sent_at to
        determine lifecycle state (null means not published / not sent)."""
        response = session.get(_url("posts", post_slug))
        return _response_text(response)

    @mcp.tool()
    def create_post(title: str, slug: str, body_md: str) -> str:
        """Create a new draft post. The post is unpublished (published_at null) and unsent
        (sent_at null) until you call publish_post / send_post."""
        response = session.post(
            _url("posts"),
            json={"title": title, "slug": slug, "body_md": body_md},
        )
        return _response_text(response)

    @mcp.tool()
    def update_post(post_slug: str, title: str, body_md: str, slug: str = "") -> str:
        """Replace title and body for an existing post. slug is optional; omit it (or send
        empty) to keep the current slug. Publishing/sending state is unchanged by this call."""
        payload: dict[str, str] = {"title": title, "body_md": body_md}
        if slug:
            payload["slug"] = slug
        response = session.put(_url("posts", post_slug), json=payload)
        return _response_text(response)

    @mcp.tool()
    def publish_post(post_slug: str) -> str:
        """Set published_at to now. The post becomes visible on the public archive.
        Does not email subscribers — use send_test or send_post for that."""
        response = session.post(_url("posts", post_slug, "publish"))
        return _response_text(response)

    @mcp.tool()
    def send_test(post_slug: str, email: str) -> str:
        """Email this post to a single address for preview. Does not mark the post as sent
        (sent_at remains null) and does not email the subscriber list.

        Prefer this over full send when you only need a personal preview."""
        response = session.post(
            _url("posts", post_slug, "send-test"),
            json={"email": email},
        )
        return _response_text(response)

    @mcp.tool()
    def send_post(post_slug: str) -> str:
        """Email the post to every confirmed subscriber and set sent_at.

        Requirements:
        - Post must already be published (published_at set) — otherwise 400.
        - Post must not already have been sent (sent_at null) — otherwise 409.

        Do not use this when you only want a personal preview; use send_test instead."""
        response = session.post(_url("posts", post_slug, "send"))
        return _response_text(response)

    @mcp.tool()
    def list_subscribers() -> str:
        """List all subscribers for the newsletter (confirmed and pending).
        Inspect confirmed_at — null means the double opt-in is still pending."""
        response = session.get(_url("subscribers"))
        return _response_text(response)

    return mcp


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
