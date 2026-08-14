import base64
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

PAGE_SIZE = 20
PAGE_MAX = 100
RSS_LIMIT = 50
CONFIRM_TTL = timedelta(days=3)


class InvalidCursor(ValueError):
    """Raised when a list cursor cannot be decoded."""


class Page(NamedTuple):
    items: list
    next_cursor: str | None


class AuditEvent(NamedTuple):
    id: int
    newsletter_id: int | None
    event_type: str
    details: str  # JSON
    created_at: datetime


class NewsletterConfig(NamedTuple):
    newsletter_id: int
    subject_prefix: str | None
    email_opener: str | None
    email_footer: str | None
    post_header_template: str | None = None
    subscribe_position: str = "end"
    public_description: str | None = None


CONFIG_DEFAULTS: dict[str, str | None] = {
    "subject_prefix": None,
    "email_opener": None,
    "email_footer": None,
    "post_header_template": None,
    "subscribe_position": "end",
    "public_description": None,
}


class Newsletter(NamedTuple):
    id: int
    slug: str
    name: str
    from_email: str
    from_name: str
    reply_to: str | None


class Post(NamedTuple):
    id: int
    newsletter_id: int
    slug: str
    title: str
    body_md: str
    published_at: datetime | None
    sent_at: datetime | None


class Subscriber(NamedTuple):
    id: int
    newsletter_id: int
    email: str
    token: str
    confirmed_at: datetime | None
    unsubscribed: bool = False
    created_at: datetime | None = None


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return PAGE_SIZE
    return max(1, min(int(limit), PAGE_MAX))


def encode_cursor(*parts: str) -> str:
    raw = "\n".join(parts).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> list[str]:
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode()
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidCursor("invalid cursor") from exc
    parts = raw.split("\n")
    if not parts or not parts[0]:
        raise InvalidCursor("invalid cursor")
    return parts


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def is_pending_expired(created_at: datetime | None, confirmed_at: datetime | None) -> bool:
    if confirmed_at is not None:
        return False
    created = as_utc(created_at)
    if created is None:
        return False
    return created < datetime.now(timezone.utc) - CONFIRM_TTL
