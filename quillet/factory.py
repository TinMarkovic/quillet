"""
Factory functions for creating the QUILLET Flask blueprint and standalone app.
All runtime configuration (DB, email, mode) is wired here.
"""

import os

from flask import Blueprint, Flask

from .cli import quillet_cli
from .db import NewsletterRepository
from .email import EmailSender


def create_blueprint(
    db: NewsletterRepository,
    email: EmailSender,
    admin_password: str,
    mode: str = "web",
    admin_ui: bool = True,
    base_url: str = "",
    name: str = "quillet",
) -> Blueprint:
    """
    Return a configured Flask Blueprint ready to register.

    Example::

        app.register_blueprint(
            create_blueprint(
                db=SQLAlchemyRepository("sqlite:///q.db"),
                email=MailgunSender(api_key="...", domain="..."),
                admin_password="secret",
            ),
            url_prefix="/newsletter",
        )
    """
    from .admin import register_admin_routes
    from .routes import register_public_routes

    bp = Blueprint(name, __name__, template_folder="templates")

    @bp.record_once
    def _on_register(state):
        app = state.app
        app.config.setdefault("QUILLET_DB", db)
        app.config.setdefault("QUILLET_EMAIL", email)
        app.config.setdefault("QUILLET_ADMIN_PASSWORD", admin_password)
        app.config.setdefault("QUILLET_MODE", mode)
        app.config.setdefault("QUILLET_ADMIN_UI", admin_ui)
        app.config.setdefault("QUILLET_BLUEPRINT_NAME", name)
        if base_url:
            app.config.setdefault("QUILLET_BASE_URL", base_url)
        app.cli.add_command(quillet_cli)

    register_public_routes(bp)
    if admin_ui and mode != "api":
        register_admin_routes(bp)

    return bp


def create_app() -> Flask:
    """
    Create a standalone Flask app driven entirely by environment variables.
    Intended for Docker / gunicorn deployments.
    """
    mode = os.environ.get("QUILLET_MODE", "web").lower()
    admin_ui = os.environ.get("QUILLET_ADMIN_UI", "true").lower() not in ("false", "0", "no")
    admin_password = os.environ.get("QUILLET_ADMIN_PASSWORD", "")
    base_url = os.environ.get("QUILLET_BASE_URL", "")

    if not admin_password:
        import warnings

        warnings.warn("QUILLET_ADMIN_PASSWORD is not set. Admin endpoints are unprotected.", stacklevel=1)

    db = _build_db()
    email = _build_email()

    app = Flask(__name__, template_folder=None)
    app.config["QUILLET_DB"] = db
    app.config["QUILLET_EMAIL"] = email
    app.config["QUILLET_ADMIN_PASSWORD"] = admin_password
    app.config["QUILLET_MODE"] = mode
    app.config["QUILLET_ADMIN_UI"] = admin_ui
    if base_url:
        app.config["QUILLET_BASE_URL"] = base_url

    bp = create_blueprint(
        db=db,
        email=email,
        admin_password=admin_password,
        mode=mode,
        admin_ui=admin_ui,
        base_url=base_url,
    )
    app.register_blueprint(bp)
    app.cli.add_command(quillet_cli)

    return app


def _build_db() -> NewsletterRepository:
    backend = os.environ.get("QUILLET_DB_BACKEND", "sqlalchemy").lower()

    if backend == "sqlalchemy":
        from .db.sqlalchemy import SQLAlchemyRepository

        db_url = os.environ.get("QUILLET_DB_URL", "sqlite:////data/quillet.db")
        return SQLAlchemyRepository(db_url)

    if backend == "supabase_rest":
        from .db.supabase_rest import SupabaseRestRepository

        url = os.environ["QUILLET_SUPABASE_URL"]
        key = os.environ["QUILLET_SUPABASE_KEY"]
        return SupabaseRestRepository(url, key)

    raise ValueError(f"Unknown QUILLET_DB_BACKEND: {backend!r}. Use 'sqlalchemy' or 'supabase_rest'.")


def _build_email() -> EmailSender:
    backend = os.environ.get("QUILLET_EMAIL_BACKEND", "smtp").lower()

    if backend == "mailgun":
        from .email.mailgun import MailgunSender

        return MailgunSender(
            api_key=os.environ["QUILLET_MAILGUN_API_KEY"],
            domain=os.environ["QUILLET_MAILGUN_DOMAIN"],
        )

    if backend == "noop":
        from .email.noop import NoopSender

        return NoopSender()

    if backend == "smtp":
        from .email.smtp import SmtpSender

        use_tls_raw = os.environ.get("QUILLET_SMTP_USE_TLS", "true").lower()
        return SmtpSender(
            from_email=os.environ["QUILLET_SMTP_FROM_EMAIL"],
            from_name=os.environ.get("QUILLET_SMTP_FROM_NAME", ""),
            host=os.environ.get("QUILLET_SMTP_HOST", "localhost"),
            port=int(os.environ.get("QUILLET_SMTP_PORT", "587")),
            username=os.environ.get("QUILLET_SMTP_USERNAME") or None,
            password=os.environ.get("QUILLET_SMTP_PASSWORD") or None,
            use_tls=use_tls_raw not in ("false", "0", "no"),
        )

    raise ValueError(f"Unknown QUILLET_EMAIL_BACKEND: {backend!r}. Use 'mailgun', 'smtp', or 'noop'.")
