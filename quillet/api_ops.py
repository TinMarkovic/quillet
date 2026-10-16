"""
In-process admin operations shared by the JSON API routes and the MCP server.

No HTTP client calls — both surfaces invoke these against the repository / email
backends directly.
"""

from __future__ import annotations

import json
import secrets
from typing import Any, NamedTuple

from flask import Flask

from .db import NewsletterRepository
from .email import EmailSender
from .email._utils import normalize_section_position, render_invite_md
from .models import PAGE_SIZE, InvalidCursor, Newsletter, Subscriber, clamp_limit


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


def _section(section_md: object, section_position: object) -> tuple[str, str]:
    if not isinstance(section_md, str) or not isinstance(section_position, str):
        raise ValueError("section_md and section_position must be strings.")
    return section_md, normalize_section_position(section_position)


def send_test(
    app: Flask,
    newsletter_slug: str,
    post_slug: str,
    email: str,
    section_md: object = "",
    section_position: object = "append",
) -> tuple[dict[str, Any], int]:
    db: NewsletterRepository = app.config["QUILLET_DB"]
    newsletter = _require_newsletter(db, newsletter_slug)
    if isinstance(newsletter, OpError):
        return _err(newsletter.error, newsletter.status)

    post = db.get_post(newsletter_slug, post_slug)
    if post is None:
        return _err("Post not found.", 404)

    test_email = email.strip() if isinstance(email, str) else ""
    if not test_email:
        return _err("email is required.", 400)

    try:
        section_text, position = _section(section_md, section_position)
    except ValueError as exc:
        return _err(str(exc), 400)

    # Lazy import — routes helpers need Flask; avoids import cycle with routes → api_ops.
    from .routes import _send_test_email

    bp_name = app.config.get("QUILLET_BLUEPRINT_NAME", "quillet")
    with app.app_context():
        with app.test_request_context("/"):
            _send_test_email(
                newsletter,
                post,
                test_email,
                bp_name,
                newsletter_slug,
                section_md=section_text,
                section_position=position,
            )
    return _ok({"ok": True})


def send_post(
    app: Flask,
    newsletter_slug: str,
    post_slug: str,
    section_md: object = "",
    section_position: object = "append",
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

    try:
        section_text, position = _section(section_md, section_position)
    except ValueError as exc:
        return _err(str(exc), 400)

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
                section_md=section_text,
                section_position=position,
            )
    db.mark_sent(post.id)
    return _ok({"ok": True, "recipients": len(subscribers)})


def _send_invite(app: Flask, newsletter: Newsletter, subscriber: Subscriber, body_md: str | None) -> None:
    from .routes import _confirm_url

    email_sender: EmailSender = app.config["QUILLET_EMAIL"]
    db: NewsletterRepository = app.config["QUILLET_DB"]
    bp_name = app.config.get("QUILLET_BLUEPRINT_NAME", "quillet")
    with app.app_context():
        with app.test_request_context("/"):
            confirm_url = _confirm_url(bp_name, newsletter.slug, subscriber.token)
    email_sender.send_confirmation(
        newsletter,
        subscriber,
        confirm_url,
        db.get_newsletter_config(newsletter.id),
        body_md=body_md,
    )


def invite_subscriber(
    app: Flask,
    newsletter_slug: str,
    email: str,
    body_md: str = "",
    *,
    reset: object = False,
) -> tuple[dict[str, Any], int]:
    if not isinstance(email, str) or not isinstance(body_md, str):
        return _err("email and body_md must be strings.", 400)
    if not isinstance(reset, bool):
        return _err("reset must be a boolean.", 400)

    address = email.strip()
    if not address:
        return _err("email is required.", 400)

    db: NewsletterRepository = app.config["QUILLET_DB"]
    newsletter = _require_newsletter(db, newsletter_slug)
    if isinstance(newsletter, OpError):
        return _err(newsletter.error, newsletter.status)

    note = body_md.strip()
    if note:
        try:
            render_invite_md(note, newsletter.name, "https://confirm.invalid/preview")
        except ValueError as exc:
            return _err(str(exc), 400)

    existing = db.find_subscriber_by_email(newsletter.id, address)
    if existing is not None and existing.confirmed_at is not None and not reset:
        return _err("Already confirmed. Pass reset to reconfirm.", 409)

    created = existing is None
    if existing is None:
        subscriber = db.add_subscriber(newsletter_slug, address, secrets.token_urlsafe(32))
    else:
        refreshed = db.reset_confirmation(newsletter.id, existing.id, resubscribe=True)
        if refreshed is None:
            return _err("Subscriber not found.", 404)
        subscriber = refreshed

    _send_invite(app, newsletter, subscriber, note or None)
    db.log_event(
        "invite",
        json.dumps({"email": address, "newsletter_slug": newsletter_slug, "reset": reset}),
        newsletter_id=newsletter.id,
    )
    return _ok({"ok": True, "subscriber": subscriber._asdict()}, 201 if created else 200)


def reset_subscriber(
    db: NewsletterRepository,
    newsletter_slug: str,
    subscriber_id: int,
) -> tuple[dict[str, Any], int]:
    newsletter = _require_newsletter(db, newsletter_slug)
    if isinstance(newsletter, OpError):
        return _err(newsletter.error, newsletter.status)

    subscriber = db.reset_confirmation(newsletter.id, subscriber_id)
    if subscriber is None:
        return _err("Subscriber not found.", 404)

    db.log_event(
        "subscriber_reset",
        json.dumps({"subscriber_id": subscriber_id, "newsletter_slug": newsletter_slug}),
        newsletter_id=newsletter.id,
    )
    return _ok({"ok": True, "subscriber": subscriber._asdict()})


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
