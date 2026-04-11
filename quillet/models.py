from datetime import datetime
from typing import NamedTuple


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
