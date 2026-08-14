"""
In-process admin operations shared by the JSON API routes and the MCP server.

No HTTP client calls — both surfaces invoke these against the repository / email
backends directly.
"""

from __future__ import annotations

import json
from typing import Any, NamedTuple

from flask import Flask

from .db import NewsletterRepository
from .email import EmailSender
from .models import PAGE_SIZE, InvalidCursor, Newsletter, clamp_limit


class OpError(NamedTuple):
    error: str
    status: int


def _ok(payload: dict[str, Any], status: int = 200) -> tuple[dict[str, Any], int]:
    return payload, status


def _err(message: str, status: int) -> tuple[dict[str, Any], int]:
    return {"error": message}, status


def _require_newsletter(db: NewsletterRepository, newsletter_slug: str) -> Newsletter | OpError:
    newsletter = db.get_newsletter(newsletter_slug)
    if newsletter is None:
        return OpError("Newsletter not found.", 404)
    return newsletter


def list_posts(
    db: NewsletterRepository,
    newsletter_slug: str,
    status: str = "all",
    cursor: str | None = None,
    limit: str | int | None = None,
) -> tuple[dict[str, Any], int]:
    newsletter = _require_newsletter(db, newsletter_slug)
    if isinstance(newsletter, OpError):
        return _err(newsletter.error, newsletter.status)

    status_key = (status or "all").strip().lower()
    allowed = {"all", "draft", "published", "unsent", "sent"}
    if status_key not in allowed:
        return _err(f"status must be one of: {', '.join(sorted(allowed))}.", 400)

    try:
        page_size = clamp_limit(int(limit)) if limit not in (None, "") else PAGE_SIZE
    except (TypeError, ValueError):
        return _err("limit must be an integer.", 400)

    try:
        page = db.list_posts(
            newsletter_slug,
            published_only=False,
            limit=page_size,
            before=cursor or None,
            status=None if status_key == "all" else status_key,
        )
    except InvalidCursor:
        return _err("invalid cursor.", 400)

    return _ok({"posts": [p._asdict() for p in page.items], "next_cursor": page.next_cursor})


def get_post(
    db: NewsletterRepository,
    newsletter_slug: str,
    post_slug: str,
) -> tuple[dict[str, Any], int]:
    newsletter = _require_newsletter(db, newsletter_slug)
    if isinstance(newsletter, OpError):
        return _err(newsletter.error, newsletter.status)

    post = db.get_post(newsletter_slug, post_slug)
    if post is None:
        return _err("Post not found.", 404)
    return _ok({"post": post._asdict()})


def create_post(
    db: NewsletterRepository,
    newsletter_slug: str,
    title: str,
    slug: str,
    body_md: str,
) -> tuple[dict[str, Any], int]:
    newsletter = _require_newsletter(db, newsletter_slug)
    if isinstance(newsletter, OpError):
        return _err(newsletter.error, newsletter.status)

    title = title.strip()
    slug = slug.strip()
    body_md = body_md.strip()
    if not title or not slug or not body_md:
        return _err("title, slug, and body_md are required.", 400)

    post = db.create_post(newsletter_slug, title, slug, body_md)
    return _ok({"post": post._asdict()}, 201)


def update_post(
    db: NewsletterRepository,
    newsletter_slug: str,
    post_slug: str,
    title: str,
    body_md: str,
    slug: str = "",
) -> tuple[dict[str, Any], int]:
    newsletter = _require_newsletter(db, newsletter_slug)
    if isinstance(newsletter, OpError):
        return _err(newsletter.error, newsletter.status)

    post = db.get_post(newsletter_slug, post_slug)
    if post is None:
        return _err("Post not found.", 404)

    title = title.strip()
    body_md = body_md.strip()
    new_slug = (slug or "").strip() or post.slug
    if not title or not body_md:
        return _err("title and body_md are required.", 400)

    updated = db.update_post(post.id, title, new_slug, body_md)
    return _ok({"post": updated._asdict()})


def publish_post(
    db: NewsletterRepository,
    newsletter_slug: str,
    post_slug: str,
) -> tuple[dict[str, Any], int]:
    newsletter = _require_newsletter(db, newsletter_slug)
    if isinstance(newsletter, OpError):
        return _err(newsletter.error, newsletter.status)

    post = db.get_post(newsletter_slug, post_slug)
    if post is None:
        return _err("Post not found.", 404)

    updated = db.publish_post(post.id)
    return _ok({"post": updated._asdict()})


def send_test(
    app: Flask,
    newsletter_slug: str,
    post_slug: str,
    email: str,
) -> tuple[dict[str, Any], int]:
    db: NewsletterRepository = app.config["QUILLET_DB"]
    newsletter = _require_newsletter(db, newsletter_slug)
    if isinstance(newsletter, OpError):
        return _err(newsletter.error, newsletter.status)

    post = db.get_post(newsletter_slug, post_slug)
    if post is None:
        return _err("Post not found.", 404)

    test_email = email.strip()
    if not test_email:
        return _err("email is required.", 400)

    # Lazy import — routes helpers need Flask; avoids import cycle with routes → api_ops.
    from .routes import _send_test_email

    bp_name = app.config.get("QUILLET_BLUEPRINT_NAME", "quillet")
    with app.app_context():
        with app.test_request_context("/"):
            _send_test_email(newsletter, post, test_email, bp_name, newsletter_slug)
    return _ok({"ok": True})


def send_post(
    app: Flask,
    newsletter_slug: str,
    post_slug: str,
) -> tuple[dict[str, Any], int]:
    from .routes import _post_list_url, _post_url, _unsubscribe_url_template

    db: NewsletterRepository = app.config["QUILLET_DB"]
    email_sender: EmailSender = app.config["QUILLET_EMAIL"]
    newsletter = _require_newsletter(db, newsletter_slug)
    if isinstance(newsletter, OpError):
        return _err(newsletter.error, newsletter.status)

    post = db.get_post(newsletter_slug, post_slug)
    if post is None:
        return _err("Post not found.", 404)
    if post.published_at is None:
        return _err("Post must be published before sending.", 400)
    if post.sent_at is not None:
        return _err("Post has already been sent.", 409)

    subscribers = db.list_confirmed_subscribers(newsletter_slug)
    bp_name = app.config.get("QUILLET_BLUEPRINT_NAME", "quillet")
    with app.app_context():
        with app.test_request_context("/"):
            email_sender.send_post(
                newsletter,
                post,
                subscribers,
                _unsubscribe_url_template(bp_name, newsletter_slug),
                db.get_newsletter_config(newsletter.id),
                post_url=_post_url(bp_name, newsletter_slug, post_slug),
                post_list_url=_post_list_url(bp_name, newsletter_slug),
            )
    db.mark_sent(post.id)
    return _ok({"ok": True, "recipients": len(subscribers)})


def list_subscribers(
    db: NewsletterRepository,
    newsletter_slug: str,
    cursor: str | None = None,
    limit: str | int | None = None,
) -> tuple[dict[str, Any], int]:
    newsletter = _require_newsletter(db, newsletter_slug)
    if isinstance(newsletter, OpError):
        return _err(newsletter.error, newsletter.status)

    try:
        page_size = clamp_limit(int(limit)) if limit not in (None, "") else PAGE_SIZE
    except (TypeError, ValueError):
        return _err("limit must be an integer.", 400)

    try:
        page = db.list_all_subscribers(newsletter_slug, limit=page_size, before=cursor or None)
    except InvalidCursor:
        return _err("invalid cursor.", 400)

    return _ok({"subscribers": [s._asdict() for s in page.items], "next_cursor": page.next_cursor})


def to_json_text(payload: dict[str, Any], status: int) -> str:
    """Serialize an op result for MCP tool responses (includes HTTP status)."""
    return json.dumps({"status": status, **payload}, default=str)
