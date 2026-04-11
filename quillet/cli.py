import click
from flask import current_app
from flask.cli import AppGroup

from .db import NewsletterRepository
from .routes import _unsubscribe_url_template

quillet_cli = AppGroup("quillet", help="Manage QUILLET newsletters.")


def _db() -> NewsletterRepository:
    return current_app.config["QUILLET_DB"]


@quillet_cli.command("create")
@click.argument("name")
@click.option("--slug", default=None, help="URL slug (defaults to lowercased name).")
@click.option("--from-email", required=True, prompt=True, help="Sender email address.")
@click.option("--from-name", default="", help="Sender display name.")
@click.option("--reply-to", default=None, help="Reply-to address.")
def create_newsletter(name: str, slug: str | None, from_email: str, from_name: str, reply_to: str | None) -> None:
    """Create a new newsletter."""
    import re

    if not slug:
        slug = re.sub(r"[\s_-]+", "-", re.sub(r"[^\w\s-]", "", name.lower().strip()))

    newsletter = _db().create_newsletter(
        slug=slug,
        name=name,
        from_email=from_email,
        from_name=from_name or name,
        reply_to=reply_to,
    )
    click.echo(f"Created newsletter '{newsletter.name}' with slug '{newsletter.slug}'.")


@quillet_cli.command("list")
def list_newsletters() -> None:
    """List all newsletters."""
    from .db.sqlalchemy import SQLAlchemyRepository

    db = _db()

    if not isinstance(db, SQLAlchemyRepository):
        click.echo("List command is only supported with SQLAlchemyRepository.")
        return

    from sqlalchemy import select

    from .db.sqlalchemy import _newsletters

    with db._engine.connect() as conn:
        rows = conn.execute(select(_newsletters)).fetchall()

    if not rows:
        click.echo("No newsletters found.")
        return

    for row in rows:
        click.echo(f"  [{row.slug}] {row.name} <{row.from_email}>")


@quillet_cli.command("send")
@click.argument("newsletter_slug")
@click.argument("post_slug")
@click.option("--force", is_flag=True, default=False, help="Re-send even if already sent.")
def send_post(newsletter_slug: str, post_slug: str, force: bool) -> None:
    """Send a published post to all confirmed subscribers."""
    from .email import EmailSender

    db = _db()
    email_sender: EmailSender = current_app.config["QUILLET_EMAIL"]

    newsletter = db.get_newsletter(newsletter_slug)
    if newsletter is None:
        raise click.ClickException(f"Newsletter not found: {newsletter_slug}")

    post = db.get_post(newsletter_slug, post_slug)
    if post is None:
        raise click.ClickException(f"Post not found: {post_slug}")

    if post.published_at is None:
        raise click.ClickException("Post must be published before sending.")

    if post.sent_at is not None and not force:
        raise click.ClickException("Post has already been sent. Use --force to re-send.")

    subscribers = db.list_confirmed_subscribers(newsletter_slug)
    if not subscribers:
        click.echo("No confirmed subscribers. Nothing sent.")
        return

    click.echo(f"Sending '{post.title}' to {len(subscribers)} subscriber(s)...")

    bp_name = current_app.config.get("QUILLET_BLUEPRINT_NAME", "quillet")
    with current_app.test_request_context("/"):
        unsubscribe_template = _unsubscribe_url_template(bp_name, newsletter_slug)

    email_sender.send_post(newsletter, post, subscribers, unsubscribe_template)
    db.mark_sent(post.id)
    click.echo("Done.")


@quillet_cli.command("subscribers")
@click.argument("newsletter_slug")
def list_subscribers(newsletter_slug: str) -> None:
    """List subscribers for a newsletter."""
    db = _db()

    newsletter = db.get_newsletter(newsletter_slug)
    if newsletter is None:
        raise click.ClickException(f"Newsletter not found: {newsletter_slug}")

    subscribers = db.list_all_subscribers(newsletter_slug)
    if not subscribers:
        click.echo("No subscribers.")
        return

    confirmed = [s for s in subscribers if s.confirmed_at]
    pending = [s for s in subscribers if not s.confirmed_at]

    click.echo(f"{len(confirmed)} confirmed, {len(pending)} pending — {newsletter.name}")
    for sub in subscribers:
        status = "✓" if sub.confirmed_at else "?"
        click.echo(f"  [{status}] {sub.email}")
