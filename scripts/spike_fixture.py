#!/usr/bin/env python3
"""
spike_fixture.py — seed / reset the Quillet spike SQLite fixture.

Creates newsletter slug ``spike`` with a known mix of posts and subscribers
for the OpenAPI vs MCP agent matrix (see llm/tickets/api_and_testing/).

Usage:
    python scripts/spike_fixture.py --reset
    python scripts/spike_fixture.py --db quillet_spike.db --reset
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys

from quillet.db.sqlalchemy import SQLAlchemyRepository
from quillet.factory import get_or_create_newsletter

DEFAULT_DB = "quillet_spike.db"
NEWSLETTER_SLUG = "spike"

DRAFTS = (
    ("seed-draft-alpha", "Seed Draft Alpha", "# Draft Alpha\n\nUnpublished draft for the spike."),
    ("seed-draft-beta", "Seed Draft Beta", "# Draft Beta\n\nAnother unpublished draft."),
    ("seed-draft-gamma", "Seed Draft Gamma", "# Draft Gamma\n\nThird unpublished draft."),
)

PUBLISHED_UNSENT = (
    ("seed-published-alpha", "Seed Published Alpha", "# Published Alpha\n\nPublished, not yet sent."),
    ("seed-published-beta", "Seed Published Beta", "# Published Beta\n\nPublished, not yet sent."),
)

PUBLISHED_SENT = (("seed-sent-alpha", "Seed Sent Alpha", "# Sent Alpha\n\nAlready full-sent to the list."),)

# (email, confirmed)
SUBSCRIBERS = (
    ("reader1@example.com", True),
    ("reader2@example.com", True),
    ("reader3@example.com", True),
    ("pending1@example.com", False),
    ("pending2@example.com", False),
)


def seed(db_path: str = DEFAULT_DB, reset: bool = False) -> None:
    if reset and os.path.exists(db_path):
        for suffix in ("", "-shm", "-wal"):
            path = f"{db_path}{suffix}" if suffix else db_path
            if os.path.exists(path):
                os.remove(path)

    db = SQLAlchemyRepository(f"sqlite:///{db_path}")
    newsletter, created = get_or_create_newsletter(
        db,
        slug=NEWSLETTER_SLUG,
        name="Spike Fixture",
        from_email="spike@example.com",
        from_name="Spike Fixture",
        reply_to="owner@example.com",
    )

    if not created and not reset:
        existing_posts = db.list_posts(NEWSLETTER_SLUG, published_only=False)
        existing_subs = db.list_all_subscribers(NEWSLETTER_SLUG)
        if existing_posts or existing_subs:
            print(
                f"Fixture already present in {db_path} "
                f"({len(existing_posts)} posts, {len(existing_subs)} subscribers). "
                "Re-run with --reset to recreate."
            )
            _print_summary(db)
            return

    for slug, title, body in DRAFTS:
        db.create_post(NEWSLETTER_SLUG, title, slug, body)

    for slug, title, body in PUBLISHED_UNSENT:
        post = db.create_post(NEWSLETTER_SLUG, title, slug, body)
        db.publish_post(post.id)

    for slug, title, body in PUBLISHED_SENT:
        post = db.create_post(NEWSLETTER_SLUG, title, slug, body)
        published = db.publish_post(post.id)
        db.mark_sent(published.id)

    for email, confirmed in SUBSCRIBERS:
        token = secrets.token_urlsafe(32)
        subscriber = db.add_subscriber(NEWSLETTER_SLUG, email, token)
        if confirmed:
            db.confirm_subscriber(subscriber.token)

    print(f"Seeded {db_path} (newsletter_id={newsletter.id})")
    _print_summary(db)


def _print_summary(db: SQLAlchemyRepository) -> None:
    posts = db.list_posts(NEWSLETTER_SLUG, published_only=False)
    drafts = [p for p in posts if p.published_at is None]
    unsent = [p for p in posts if p.published_at is not None and p.sent_at is None]
    sent = [p for p in posts if p.sent_at is not None]
    subscribers = db.list_all_subscribers(NEWSLETTER_SLUG)
    confirmed = [s for s in subscribers if s.confirmed_at is not None]

    print(
        f"  drafts={len(drafts)}  published_unsent={len(unsent)}  "
        f"sent={len(sent)}  subscribers={len(subscribers)} "
        f"(confirmed={len(confirmed)}, pending={len(subscribers) - len(confirmed)})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed / reset the Quillet spike fixture.")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite path (default: {DEFAULT_DB})")
    parser.add_argument("--reset", action="store_true", help="Delete existing DB file and reseed.")
    args = parser.parse_args()

    seed(db_path=args.db, reset=args.reset)
    return 0


if __name__ == "__main__":
    sys.exit(main())
