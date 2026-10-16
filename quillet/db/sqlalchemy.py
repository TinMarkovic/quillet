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
    and_,
    case,
    cast,
    create_engine,
    delete,
    event,
    func,
    inspect,
    or_,
    select,
    text,
    update,
)

from ..models import (
    CONFIG_DEFAULTS,
    CONFIRM_TTL,
    PAGE_SIZE,
    AuditEvent,
    InvalidCursor,
    Newsletter,
    NewsletterConfig,
    Page,
    Post,
    Subscriber,
    clamp_limit,
    decode_cursor,
    encode_cursor,
    is_pending_expired,
)

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
    Column("created_at", DateTime, nullable=True),
)

_newsletter_config = Table(
    "newsletter_config",
    metadata,
    Column("newsletter_id", Integer, ForeignKey("newsletters.id"), nullable=False),
    Column("key", String(128), nullable=False),
    Column("value", Text, nullable=True),
    UniqueConstraint("newsletter_id", "key"),
)

_audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("newsletter_id", Integer, ForeignKey("newsletters.id"), nullable=True),
    Column("event_type", String(64), nullable=False),
    Column("details", Text, nullable=False, default="{}"),
    Column("created_at", DateTime, nullable=False),
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
        created_at=row.created_at,
    )


def _row_to_audit_event(row) -> AuditEvent:
    return AuditEvent(
        id=row.id,
        newsletter_id=row.newsletter_id,
        event_type=row.event_type,
        details=row.details,
        created_at=row.created_at,
    )


def _assemble_newsletter_config(newsletter_id: int, rows) -> NewsletterConfig:
    kv = {row.key: row.value for row in rows}
    merged = {key: kv.get(key, default) for key, default in CONFIG_DEFAULTS.items()}
    return NewsletterConfig(
        newsletter_id=newsletter_id,
        subject_prefix=merged["subject_prefix"],
        email_opener=merged["email_opener"],
        email_footer=merged["email_footer"],
        post_header_template=merged["post_header_template"],
        subscribe_position=merged["subscribe_position"] or "end",
        public_description=merged["public_description"],
    )


def _ensure_subscriber_created_at(engine) -> None:
    columns = {col["name"] for col in inspect(engine).get_columns("subscribers")}
    with engine.begin() as conn:
        if "created_at" not in columns:
            conn.execute(text("ALTER TABLE subscribers ADD COLUMN created_at TIMESTAMP"))
        # ponytail: skip pending NULLs — stamping them to NOW restarts the 3-day TTL
        # (why production never purged pre-migration rows). Purge deletes those instead.
        conn.execute(
            text(
                "UPDATE subscribers SET created_at = CURRENT_TIMESTAMP "
                "WHERE created_at IS NULL AND confirmed_at IS NOT NULL"
            )
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _cutoff_naive() -> datetime:
    return (_utc_now() - CONFIRM_TTL).replace(tzinfo=None)


def _filter_posts(query, published_only: bool, status: str | None):
    if published_only:
        return query.where(_posts.c.published_at.isnot(None))
    if status in (None, "all"):
        return query
    if status == "draft":
        return query.where(_posts.c.published_at.is_(None))
    if status == "published":
        return query.where(_posts.c.published_at.isnot(None))
    if status == "unsent":
        return query.where(_posts.c.published_at.isnot(None), _posts.c.sent_at.is_(None))
    if status == "sent":
        return query.where(_posts.c.sent_at.isnot(None))
    return query


def _sort_timestamp(column, sqlite: bool):
    """Keyset timestamp expression.

    SQLite stores datetimes as text. SQLAlchemy's bind always writes six
    fractional digits; CURRENT_TIMESTAMP does not. Pad the short form so
    equality and ORDER BY use one fixed-width string.
    """
    if not sqlite:
        return column
    # ponytail: text pad, not a column rewrite. Ceiling is mixed separators
    # (T vs space, offsets). Upgrade path: store one format and compare natively.
    text_col = cast(column, String)
    return case(
        (func.instr(text_col, ".") == 0, text_col.op("||")(".000000")),
        else_=text_col,
    )


def _cursor_text(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")


def _before_timestamp(column, id_column, when: datetime, row_id: int, sqlite: bool):
    key = _sort_timestamp(column, sqlite)
    bound: datetime | str = _cursor_text(when) if sqlite else when
    return or_(key < bound, and_(key == bound, id_column < row_id))


def _apply_post_cursor(query, published_only: bool, before: str | None, *, sqlite: bool):
    if not before:
        return query
    parts = decode_cursor(before)
    try:
        if published_only:
            if len(parts) != 3 or parts[0] != "p":
                raise InvalidCursor("invalid cursor")
            published_at = datetime.fromisoformat(parts[1])
            post_id = int(parts[2])
            return query.where(_before_timestamp(_posts.c.published_at, _posts.c.id, published_at, post_id, sqlite))
        if len(parts) != 2 or parts[0] != "i":
            raise InvalidCursor("invalid cursor")
        return query.where(_posts.c.id < int(parts[1]))
    except InvalidCursor:
        raise
    except (TypeError, ValueError) as exc:
        raise InvalidCursor("invalid cursor") from exc


class SQLAlchemyRepository:
    def __init__(self, db_url: str) -> None:
        is_sqlite = db_url.startswith("sqlite")
        self._engine = create_engine(
            db_url,
            connect_args={"timeout": 10} if is_sqlite else {},
        )

        if is_sqlite:

            @event.listens_for(self._engine, "connect")
            def set_wal_mode(dbapi_conn, connection_record):
                dbapi_conn.execute("PRAGMA journal_mode=WAL")

        metadata.create_all(self._engine)
        _ensure_subscriber_created_at(self._engine)

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
            "post_header_template": config.post_header_template,
            "subscribe_position": config.subscribe_position,
            "public_description": config.public_description,
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

    def list_newsletter_slugs(self) -> list[str]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(_newsletters.c.slug)).fetchall()
        return [row.slug for row in rows]

    # --- Posts ---

    def list_posts(
        self,
        newsletter_slug: str,
        published_only: bool = True,
        *,
        limit: int = PAGE_SIZE,
        before: str | None = None,
        status: str | None = None,
    ) -> Page:
        page_size = clamp_limit(limit)
        sqlite = self._engine.dialect.name == "sqlite"
        with self._engine.connect() as conn:
            newsletter = conn.execute(select(_newsletters).where(_newsletters.c.slug == newsletter_slug)).one_or_none()
            if newsletter is None:
                return Page([], None)

            query = select(_posts).where(_posts.c.newsletter_id == newsletter.id)
            query = _filter_posts(query, published_only=published_only, status=status)
            query = _apply_post_cursor(query, published_only=published_only, before=before, sqlite=sqlite)
            if published_only:
                published_key = _sort_timestamp(_posts.c.published_at, sqlite)
                query = query.order_by(published_key.desc(), _posts.c.id.desc())
            else:
                query = query.order_by(_posts.c.id.desc())
            rows = conn.execute(query.limit(page_size + 1)).fetchall()

        posts = [_row_to_post(r) for r in rows[:page_size]]
        if len(rows) <= page_size:
            return Page(posts, None)
        last = posts[-1]
        if published_only:
            cursor = encode_cursor("p", last.published_at.isoformat(), str(last.id))
        else:
            cursor = encode_cursor("i", str(last.id))
        return Page(posts, cursor)

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
        self.purge_expired_pending(newsletter.id)
        with self._engine.begin() as conn:
            result = conn.execute(
                _subscribers.insert().values(
                    newsletter_id=newsletter.id,
                    email=email,
                    token=token,
                    confirmed_at=None,
                    unsubscribed=False,
                    created_at=_utc_now(),
                )
            )
            row = conn.execute(select(_subscribers).where(_subscribers.c.id == result.inserted_primary_key[0])).one()
        return _row_to_subscriber(row)

    def confirm_subscriber(self, token: str) -> Subscriber | None:
        with self._engine.begin() as conn:
            row = conn.execute(select(_subscribers).where(_subscribers.c.token == token)).one_or_none()
            if row is None:
                return None
            subscriber = _row_to_subscriber(row)
        self.purge_expired_pending(subscriber.newsletter_id)
        if is_pending_expired(subscriber.created_at, subscriber.confirmed_at):
            return None
        with self._engine.begin() as conn:
            conn.execute(update(_subscribers).where(_subscribers.c.token == token).values(confirmed_at=_utc_now()))
            row = conn.execute(select(_subscribers).where(_subscribers.c.token == token)).one_or_none()
        if row is None:
            return None
        return _row_to_subscriber(row)

    def get_subscriber_by_token(self, token: str) -> Subscriber | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(_subscribers).where(_subscribers.c.token == token)).one_or_none()
        if row is None:
            return None
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

    def list_all_subscribers(
        self,
        newsletter_slug: str,
        *,
        limit: int = PAGE_SIZE,
        before: str | None = None,
    ) -> Page:
        with self._engine.connect() as conn:
            newsletter = conn.execute(select(_newsletters).where(_newsletters.c.slug == newsletter_slug)).one_or_none()
            if newsletter is None:
                return Page([], None)
            newsletter_id = newsletter.id
        self.purge_expired_pending(newsletter_id)
        page_size = clamp_limit(limit)
        sqlite = self._engine.dialect.name == "sqlite"
        query = select(_subscribers).where(_subscribers.c.newsletter_id == newsletter_id)
        if before:
            parts = decode_cursor(before)
            try:
                if len(parts) != 3 or parts[0] != "s":
                    raise InvalidCursor("invalid cursor")
                created_at = datetime.fromisoformat(parts[1])
                sub_id = int(parts[2])
            except InvalidCursor:
                raise
            except (TypeError, ValueError) as exc:
                raise InvalidCursor("invalid cursor") from exc
            query = query.where(
                _before_timestamp(_subscribers.c.created_at, _subscribers.c.id, created_at, sub_id, sqlite)
            )
        created_key = _sort_timestamp(_subscribers.c.created_at, sqlite)
        query = query.order_by(created_key.desc(), _subscribers.c.id.desc()).limit(page_size + 1)
        with self._engine.connect() as conn:
            rows = conn.execute(query).fetchall()
        items = [_row_to_subscriber(r) for r in rows[:page_size]]
        if len(rows) <= page_size:
            return Page(items, None)
        last = items[-1]
        created = last.created_at or _utc_now()
        return Page(items, encode_cursor("s", created.isoformat(), str(last.id)))

    def purge_expired_pending(self, newsletter_id: int) -> int:
        with self._engine.begin() as conn:
            result = conn.execute(
                delete(_subscribers).where(
                    (_subscribers.c.newsletter_id == newsletter_id)
                    & (_subscribers.c.confirmed_at.is_(None))
                    & or_(
                        _subscribers.c.created_at.is_(None),
                        _subscribers.c.created_at < _cutoff_naive(),
                    )
                )
            )
        return result.rowcount or 0

    def unsubscribe(self, token: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(update(_subscribers).where(_subscribers.c.token == token).values(unsubscribed=True))

    def delete_subscriber(self, subscriber_id: int) -> None:
        with self._engine.begin() as conn:
            conn.execute(_subscribers.delete().where(_subscribers.c.id == subscriber_id))

    # --- Audit log ---

    def log_event(
        self,
        event_type: str,
        details: str = "{}",
        newsletter_id: int | None = None,
    ) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                _audit_log.insert().values(
                    newsletter_id=newsletter_id,
                    event_type=event_type,
                    details=details,
                    created_at=datetime.now(timezone.utc),
                )
            )

    # --- Subscriber lookup ---

    def find_subscriber_by_email(self, newsletter_id: int, email: str) -> Subscriber | None:
        self.purge_expired_pending(newsletter_id)
        with self._engine.connect() as conn:
            row = conn.execute(
                select(_subscribers)
                .where((_subscribers.c.newsletter_id == newsletter_id) & (_subscribers.c.email == email))
                .order_by(_subscribers.c.id.desc())
            ).first()
        if row is None:
            return None
        return _row_to_subscriber(row)

    def reset_confirmation(
        self,
        newsletter_id: int,
        subscriber_id: int,
        *,
        resubscribe: bool = False,
    ) -> Subscriber | None:
        values: dict = {"confirmed_at": None, "created_at": _utc_now()}
        if resubscribe:
            values["unsubscribed"] = False
        with self._engine.begin() as conn:
            result = conn.execute(
                update(_subscribers)
                .where((_subscribers.c.id == subscriber_id) & (_subscribers.c.newsletter_id == newsletter_id))
                .values(**values)
            )
            if not result.rowcount:
                return None
            row = conn.execute(select(_subscribers).where(_subscribers.c.id == subscriber_id)).one()
        return _row_to_subscriber(row)

    def get_subscriber_by_email(self, newsletter_id: int, email: str) -> Subscriber | None:
        self.purge_expired_pending(newsletter_id)
        with self._engine.connect() as conn:
            row = conn.execute(
                select(_subscribers).where(
                    (_subscribers.c.newsletter_id == newsletter_id)
                    & (_subscribers.c.email == email)
                    & (_subscribers.c.unsubscribed == False)  # noqa: E712
                )
            ).one_or_none()
        if row is None:
            return None
        return _row_to_subscriber(row)
