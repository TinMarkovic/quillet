from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    select,
    update,
)

from ..models import CONFIG_DEFAULTS, Newsletter, NewsletterConfig, Post, Subscriber

metadata = MetaData()

_newsletters = Table(
    "newsletters",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("slug", String(128), unique=True, nullable=False),
    Column("name", String(256), nullable=False),
    Column("from_email", String(256), nullable=False),
    Column("from_name", String(256), nullable=False),
    Column("reply_to", String(256), nullable=True),
)

_posts = Table(
    "posts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("newsletter_id", Integer, ForeignKey("newsletters.id"), nullable=False),
    Column("slug", String(128), nullable=False),
    Column("title", String(512), nullable=False),
    Column("body_md", Text, nullable=False),
    Column("published_at", DateTime, nullable=True),
    Column("sent_at", DateTime, nullable=True),
)

_subscribers = Table(
    "subscribers",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("newsletter_id", Integer, ForeignKey("newsletters.id"), nullable=False),
    Column("email", String(256), nullable=False),
    Column("token", String(64), unique=True, nullable=False),
    Column("confirmed_at", DateTime, nullable=True),
    Column("unsubscribed", Boolean, default=False, nullable=False),
)

_newsletter_config = Table(
    "newsletter_config",
    metadata,
    Column("newsletter_id", Integer, ForeignKey("newsletters.id"), nullable=False),
    Column("key", String(128), nullable=False),
    Column("value", Text, nullable=True),
    UniqueConstraint("newsletter_id", "key"),
)


def _row_to_newsletter(row) -> Newsletter:
    return Newsletter(
        id=row.id,
        slug=row.slug,
        name=row.name,
        from_email=row.from_email,
        from_name=row.from_name,
        reply_to=row.reply_to,
    )


def _row_to_post(row) -> Post:
    return Post(
        id=row.id,
        newsletter_id=row.newsletter_id,
        slug=row.slug,
        title=row.title,
        body_md=row.body_md,
        published_at=row.published_at,
        sent_at=row.sent_at,
    )


def _row_to_subscriber(row) -> Subscriber:
    return Subscriber(
        id=row.id,
        newsletter_id=row.newsletter_id,
        email=row.email,
        token=row.token,
        unsubscribed=bool(row.unsubscribed),
        confirmed_at=row.confirmed_at,
    )


def _assemble_newsletter_config(newsletter_id: int, rows) -> NewsletterConfig:
    kv = {row.key: row.value for row in rows}
    merged = {key: kv.get(key, default) for key, default in CONFIG_DEFAULTS.items()}
    return NewsletterConfig(
        newsletter_id=newsletter_id,
        subject_prefix=merged["subject_prefix"],
        email_opener=merged["email_opener"],
        email_footer=merged["email_footer"],
    )


class SQLAlchemyRepository:
    def __init__(self, db_url: str) -> None:
        is_sqlite = db_url.startswith("sqlite")
        self._engine = create_engine(
            db_url,
            connect_args={"timeout": 10} if is_sqlite else {},
            execution_options={"isolation_level": None} if is_sqlite else {},
        )

        if is_sqlite:

            @event.listens_for(self._engine, "connect")
            def set_wal_mode(dbapi_conn, connection_record):
                dbapi_conn.execute("PRAGMA journal_mode=WAL")

        metadata.create_all(self._engine)

    # --- Newsletters ---

    def create_newsletter(
        self,
        slug: str,
        name: str,
        from_email: str,
        from_name: str,
        reply_to: str | None,
    ) -> Newsletter:
        with self._engine.begin() as conn:
            result = conn.execute(
                _newsletters.insert().values(
                    slug=slug,
                    name=name,
                    from_email=from_email,
                    from_name=from_name,
                    reply_to=reply_to,
                )
            )
            row = conn.execute(select(_newsletters).where(_newsletters.c.id == result.inserted_primary_key[0])).one()
        return _row_to_newsletter(row)

    def update_newsletter(
        self,
        newsletter_id: int,
        name: str,
        from_name: str,
        from_email: str,
        reply_to: str | None,
    ) -> Newsletter:
        with self._engine.begin() as conn:
            conn.execute(
                update(_newsletters)
                .where(_newsletters.c.id == newsletter_id)
                .values(name=name, from_name=from_name, from_email=from_email, reply_to=reply_to)
            )
            row = conn.execute(select(_newsletters).where(_newsletters.c.id == newsletter_id)).one()
        return _row_to_newsletter(row)

    def get_newsletter_config(self, newsletter_id: int) -> NewsletterConfig:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(_newsletter_config).where(_newsletter_config.c.newsletter_id == newsletter_id)
            ).fetchall()
        return _assemble_newsletter_config(newsletter_id, rows)

    def save_newsletter_config(self, config: NewsletterConfig) -> None:
        kv = {
            "subject_prefix": config.subject_prefix,
            "email_opener": config.email_opener,
            "email_footer": config.email_footer,
        }
        with self._engine.begin() as conn:
            for key, value in kv.items():
                conn.execute(
                    _newsletter_config.delete().where(
                        (_newsletter_config.c.newsletter_id == config.newsletter_id) & (_newsletter_config.c.key == key)
                    )
                )
                conn.execute(
                    _newsletter_config.insert().values(
                        newsletter_id=config.newsletter_id,
                        key=key,
                        value=value,
                    )
                )

    def get_newsletter(self, slug: str) -> Newsletter | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(_newsletters).where(_newsletters.c.slug == slug)).one_or_none()
        if row is None:
            return None
        return _row_to_newsletter(row)

    # --- Posts ---

    def list_posts(self, newsletter_slug: str, published_only: bool = True) -> list[Post]:
        with self._engine.connect() as conn:
            newsletter = conn.execute(select(_newsletters).where(_newsletters.c.slug == newsletter_slug)).one_or_none()
            if newsletter is None:
                return []

            query = select(_posts).where(_posts.c.newsletter_id == newsletter.id)
            if published_only:
                query = query.where(_posts.c.published_at.isnot(None))
            query = query.order_by(_posts.c.published_at.desc())
            rows = conn.execute(query).fetchall()

        return [_row_to_post(r) for r in rows]

    def get_post(self, newsletter_slug: str, post_slug: str) -> Post | None:
        with self._engine.connect() as conn:
            newsletter = conn.execute(select(_newsletters).where(_newsletters.c.slug == newsletter_slug)).one_or_none()
            if newsletter is None:
                return None

            row = conn.execute(
                select(_posts).where((_posts.c.newsletter_id == newsletter.id) & (_posts.c.slug == post_slug))
            ).one_or_none()

        if row is None:
            return None
        return _row_to_post(row)

    def create_post(self, newsletter_slug: str, title: str, slug: str, body_md: str) -> Post:
        with self._engine.begin() as conn:
            newsletter = conn.execute(select(_newsletters).where(_newsletters.c.slug == newsletter_slug)).one()
            result = conn.execute(
                _posts.insert().values(
                    newsletter_id=newsletter.id,
                    slug=slug,
                    title=title,
                    body_md=body_md,
                    published_at=None,
                    sent_at=None,
                )
            )
            row = conn.execute(select(_posts).where(_posts.c.id == result.inserted_primary_key[0])).one()
        return _row_to_post(row)

    def update_post(self, post_id: int, title: str, slug: str, body_md: str) -> Post:
        with self._engine.begin() as conn:
            conn.execute(update(_posts).where(_posts.c.id == post_id).values(title=title, slug=slug, body_md=body_md))
            row = conn.execute(select(_posts).where(_posts.c.id == post_id)).one()
        return _row_to_post(row)

    def publish_post(self, post_id: int) -> Post:
        with self._engine.begin() as conn:
            conn.execute(update(_posts).where(_posts.c.id == post_id).values(published_at=datetime.now(timezone.utc)))
            row = conn.execute(select(_posts).where(_posts.c.id == post_id)).one()
        return _row_to_post(row)

    def mark_sent(self, post_id: int) -> None:
        with self._engine.begin() as conn:
            conn.execute(update(_posts).where(_posts.c.id == post_id).values(sent_at=datetime.now(timezone.utc)))

    def delete_post(self, post_id: int) -> None:
        with self._engine.begin() as conn:
            conn.execute(_posts.delete().where(_posts.c.id == post_id))

    # --- Subscribers ---

    def add_subscriber(self, newsletter_slug: str, email: str, token: str) -> Subscriber:
        with self._engine.begin() as conn:
            newsletter = conn.execute(select(_newsletters).where(_newsletters.c.slug == newsletter_slug)).one()
            result = conn.execute(
                _subscribers.insert().values(
                    newsletter_id=newsletter.id,
                    email=email,
                    token=token,
                    confirmed_at=None,
                    unsubscribed=False,
                )
            )
            row = conn.execute(select(_subscribers).where(_subscribers.c.id == result.inserted_primary_key[0])).one()
        return _row_to_subscriber(row)

    def confirm_subscriber(self, token: str) -> Subscriber | None:
        with self._engine.begin() as conn:
            row = conn.execute(select(_subscribers).where(_subscribers.c.token == token)).one_or_none()
            if row is None:
                return None
            conn.execute(
                update(_subscribers)
                .where(_subscribers.c.token == token)
                .values(confirmed_at=datetime.now(timezone.utc))
            )
            row = conn.execute(select(_subscribers).where(_subscribers.c.token == token)).one()
        return _row_to_subscriber(row)

    def list_confirmed_subscribers(self, newsletter_slug: str) -> list[Subscriber]:
        with self._engine.connect() as conn:
            newsletter = conn.execute(select(_newsletters).where(_newsletters.c.slug == newsletter_slug)).one_or_none()
            if newsletter is None:
                return []
            rows = conn.execute(
                select(_subscribers).where(
                    (_subscribers.c.newsletter_id == newsletter.id)
                    & (_subscribers.c.confirmed_at.isnot(None))
                    & (_subscribers.c.unsubscribed == False)  # noqa: E712
                )
            ).fetchall()
        return [_row_to_subscriber(r) for r in rows]

    def list_all_subscribers(self, newsletter_slug: str) -> list[Subscriber]:
        with self._engine.connect() as conn:
            newsletter = conn.execute(select(_newsletters).where(_newsletters.c.slug == newsletter_slug)).one_or_none()
            if newsletter is None:
                return []
            rows = conn.execute(select(_subscribers).where(_subscribers.c.newsletter_id == newsletter.id)).fetchall()
        return [_row_to_subscriber(r) for r in rows]

    def unsubscribe(self, token: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(update(_subscribers).where(_subscribers.c.token == token).values(unsubscribed=True))

    def delete_subscriber(self, subscriber_id: int) -> None:
        with self._engine.begin() as conn:
            conn.execute(_subscribers.delete().where(_subscribers.c.id == subscriber_id))
