"""
QUILLET — Flask newsletter + microblog package.

Quick start::

    from quillet import create_blueprint
    from quillet.db.sqlalchemy import SQLAlchemyRepository
    from quillet.email.mailgun import MailgunSender

    app.register_blueprint(
        create_blueprint(
            db=SQLAlchemyRepository("sqlite:///newsletter.db"),
            email=MailgunSender(api_key="...", domain="..."),
            admin_password="secret",
        ),
        url_prefix="/newsletter",
    )
"""

from .factory import create_app, create_blueprint

__all__ = ["create_app", "create_blueprint"]
