"""
QUILLET — Flask newsletter + microblog package.

Quick start::

    from quillet import create_blueprint, get_or_create_newsletter
    from quillet.db.sqlalchemy import SQLAlchemyRepository
    from quillet.email.mailgun import MailgunSender

    db = SQLAlchemyRepository("sqlite:///newsletter.db")
    get_or_create_newsletter(db, slug="blog", name="My Blog", from_email="hi@example.com")

    app.register_blueprint(
        create_blueprint(
            db=db,
            email=MailgunSender(api_key="...", domain="..."),
            admin_password="secret",
        ),
        url_prefix="/newsletter",
    )
"""

from .factory import create_app, create_blueprint, get_or_create_newsletter

__all__ = ["create_app", "create_blueprint", "get_or_create_newsletter"]
