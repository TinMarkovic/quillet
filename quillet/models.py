from datetime import datetime
from typing import NamedTuple


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


CONFIG_DEFAULTS: dict[str, str | None] = {
    "subject_prefix": None,
    "email_opener": None,
    "email_footer": None,
    "post_header_template": None,
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
