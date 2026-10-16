from datetime import datetime, timezone

import requests

from ..models import (
    CONFIRM_TTL,
    PAGE_SIZE,
    InvalidCursor,
    Newsletter,
    Page,
    Post,
    Subscriber,
    clamp_limit,
    decode_cursor,
    encode_cursor,
    is_pending_expired,
)

_DATETIME_FMT = "%Y-%m-%dT%H:%M:%S"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.rstrip("Z")).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _fmt_dt(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.strftime(_DATETIME_FMT)


class SupabaseRestRepository:
    """
    Repository backed by the Supabase REST (PostgREST) API.

    Useful for serverless/API-key-only deployments where a persistent
    DB connection string is not available.

    Requires tables: newsletters, posts, subscribers
    (same schema as SQLAlchemyRepository). Hosts must include
    ``subscribers.created_at`` (timestamptz). Pending rows older than 3 days
    (or with a null ``created_at``) are hard-deleted on subscriber-touch.
    """

    def __init__(self, supabase_url: str, supabase_key: str) -> None:
        self._url = supabase_url.rstrip("/")
        self._headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    def _get(self, table: str, params: dict) -> list[dict]:
        resp = requests.get(
            f"{self._url}/rest/v1/{table}",
            headers=self._headers,
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def _post(self, table: str, data: dict) -> dict:
        resp = requests.post(
            f"{self._url}/rest/v1/{table}",
            headers=self._headers,
            json=data,
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if isinstance(rows, list) else rows

    def _patch(self, table: str, match: dict, data: dict) -> dict:
        params = {k: f"eq.{v}" for k, v in match.items()}
        resp = requests.patch(
            f"{self._url}/rest/v1/{table}",
            headers=self._headers,
            params=params,
            json=data,
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            raise ValueError(f"No rows updated in {table} for {match}")
        return rows[0] if isinstance(rows, list) else rows

    def _delete(self, table: str, match: dict) -> None:
        params = {k: f"eq.{v}" for k, v in match.items()}
        resp = requests.delete(
            f"{self._url}/rest/v1/{table}",
            headers=self._headers,
            params=params,
            timeout=10,
        )
        resp.raise_for_status()

    def _row_to_newsletter(self, row: dict) -> Newsletter:
        return Newsletter(
            id=row["id"],
            slug=row["slug"],
            name=row["name"],
            from_email=row["from_email"],
            from_name=row["from_name"],
            reply_to=row.get("reply_to"),
        )

    def _row_to_post(self, row: dict) -> Post:
        return Post(
            id=row["id"],
            newsletter_id=row["newsletter_id"],
            slug=row["slug"],
            title=row["title"],
            body_md=row["body_md"],
            published_at=_parse_dt(row.get("published_at")),
            sent_at=_parse_dt(row.get("sent_at")),
        )

    def _row_to_subscriber(self, row: dict) -> Subscriber:
        return Subscriber(
            id=row["id"],
            newsletter_id=row["newsletter_id"],
            email=row["email"],
            token=row["token"],
            confirmed_at=_parse_dt(row.get("confirmed_at")),
            unsubscribed=bool(row.get("unsubscribed", False)),
            created_at=_parse_dt(row.get("created_at")),
        )

    def _get_newsletter_id(self, slug: str) -> int | None:
        rows = self._get("newsletters", {"slug": f"eq.{slug}", "select": "id"})
        if not rows:
            return None
        return rows[0]["id"]

    # --- Newsletters ---

    def create_newsletter(
        self,
        slug: str,
        name: str,
        from_email: str,
        from_name: str,
        reply_to: str | None,
    ) -> Newsletter:
        row = self._post(
            "newsletters",
            {"slug": slug, "name": name, "from_email": from_email, "from_name": from_name, "reply_to": reply_to},
        )
        return self._row_to_newsletter(row)

    def get_newsletter(self, slug: str) -> Newsletter | None:
        rows = self._get("newsletters", {"slug": f"eq.{slug}"})
        if not rows:
            return None
        return self._row_to_newsletter(rows[0])

    def list_newsletter_slugs(self) -> list[str]:
        rows = self._get("newsletters", {"select": "slug"})
        return [row["slug"] for row in rows]

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
        newsletter_id = self._get_newsletter_id(newsletter_slug)
        if newsletter_id is None:
            return Page([], None)

        page_size = clamp_limit(limit)
        params: dict = {
            "newsletter_id": f"eq.{newsletter_id}",
            "limit": str(page_size + 1),
        }
        if published_only or status in ("published", "unsent", "sent"):
            params["published_at"] = "not.is.null"
        if status == "draft":
            params["published_at"] = "is.null"
        if status == "unsent":
            params["sent_at"] = "is.null"
        if status == "sent":
            params["sent_at"] = "not.is.null"

        if published_only:
            params["order"] = "published_at.desc,id.desc"
        else:
            params["order"] = "id.desc"

        if before:
            parts = decode_cursor(before)
            if published_only:
                if len(parts) != 3 or parts[0] != "p":
                    raise InvalidCursor("invalid cursor")
                ts = parts[1]
                post_id = parts[2]
                params["or"] = f"(published_at.lt.{ts},and(published_at.eq.{ts},id.lt.{post_id}))"
            else:
                if len(parts) != 2 or parts[0] != "i":
                    raise InvalidCursor("invalid cursor")
                params["id"] = f"lt.{parts[1]}"

        rows = self._get("posts", params)
        posts = [self._row_to_post(r) for r in rows[:page_size]]
        if len(rows) <= page_size:
            return Page(posts, None)
        last = posts[-1]
        if published_only:
            cursor = encode_cursor("p", last.published_at.isoformat(), str(last.id))
        else:
            cursor = encode_cursor("i", str(last.id))
        return Page(posts, cursor)

    def get_post(self, newsletter_slug: str, post_slug: str) -> Post | None:
        newsletter_id = self._get_newsletter_id(newsletter_slug)
        if newsletter_id is None:
            return None
        rows = self._get("posts", {"newsletter_id": f"eq.{newsletter_id}", "slug": f"eq.{post_slug}"})
        if not rows:
            return None
        return self._row_to_post(rows[0])

    def create_post(self, newsletter_slug: str, title: str, slug: str, body_md: str) -> Post:
        newsletter_id = self._get_newsletter_id(newsletter_slug)
        if newsletter_id is None:
            raise ValueError(f"Newsletter not found: {newsletter_slug}")
        row = self._post(
            "posts",
            {"newsletter_id": newsletter_id, "title": title, "slug": slug, "body_md": body_md},
        )
        return self._row_to_post(row)

    def update_post(self, post_id: int, title: str, slug: str, body_md: str) -> Post:
        row = self._patch("posts", {"id": post_id}, {"title": title, "slug": slug, "body_md": body_md})
        return self._row_to_post(row)

    def publish_post(self, post_id: int) -> Post:
        now = _fmt_dt(datetime.now(timezone.utc))
        row = self._patch("posts", {"id": post_id}, {"published_at": now})
        return self._row_to_post(row)

    def mark_sent(self, post_id: int) -> None:
        now = _fmt_dt(datetime.now(timezone.utc))
        self._patch("posts", {"id": post_id}, {"sent_at": now})

    def delete_post(self, post_id: int) -> None:
        self._delete("posts", {"id": post_id})

    # --- Subscribers ---

    def add_subscriber(self, newsletter_slug: str, email: str, token: str) -> Subscriber:
        newsletter_id = self._get_newsletter_id(newsletter_slug)
        if newsletter_id is None:
            raise ValueError(f"Newsletter not found: {newsletter_slug}")
        self.purge_expired_pending(newsletter_id)
        row = self._post(
            "subscribers",
            {
                "newsletter_id": newsletter_id,
                "email": email,
                "token": token,
                "unsubscribed": False,
                "created_at": _fmt_dt(datetime.now(timezone.utc)),
            },
        )
        return self._row_to_subscriber(row)

    def confirm_subscriber(self, token: str) -> Subscriber | None:
        rows = self._get("subscribers", {"token": f"eq.{token}"})
        if not rows:
            return None
        subscriber = self._row_to_subscriber(rows[0])
        self.purge_expired_pending(subscriber.newsletter_id)
        if is_pending_expired(subscriber.created_at, subscriber.confirmed_at):
            return None
        now = _fmt_dt(datetime.now(timezone.utc))
        row = self._patch("subscribers", {"token": token}, {"confirmed_at": now})
        return self._row_to_subscriber(row)

    def get_subscriber_by_token(self, token: str) -> Subscriber | None:
        rows = self._get("subscribers", {"token": f"eq.{token}"})
        if not rows:
            return None
        return self._row_to_subscriber(rows[0])

    def list_confirmed_subscribers(self, newsletter_slug: str) -> list[Subscriber]:
        newsletter_id = self._get_newsletter_id(newsletter_slug)
        if newsletter_id is None:
            return []
        rows = self._get(
            "subscribers",
            {
                "newsletter_id": f"eq.{newsletter_id}",
                "confirmed_at": "not.is.null",
                "unsubscribed": "eq.false",
            },
        )
        return [self._row_to_subscriber(r) for r in rows]

    def list_all_subscribers(
        self,
        newsletter_slug: str,
        *,
        limit: int = PAGE_SIZE,
        before: str | None = None,
    ) -> Page:
        newsletter_id = self._get_newsletter_id(newsletter_slug)
        if newsletter_id is None:
            return Page([], None)
        self.purge_expired_pending(newsletter_id)
        page_size = clamp_limit(limit)
        params: dict = {
            "newsletter_id": f"eq.{newsletter_id}",
            "order": "created_at.desc,id.desc",
            "limit": str(page_size + 1),
        }
        if before:
            parts = decode_cursor(before)
            if len(parts) != 3 or parts[0] != "s":
                raise InvalidCursor("invalid cursor")
            params["or"] = f"(created_at.lt.{parts[1]},and(created_at.eq.{parts[1]},id.lt.{parts[2]}))"
        rows = self._get("subscribers", params)
        items = [self._row_to_subscriber(r) for r in rows[:page_size]]
        if len(rows) <= page_size:
            return Page(items, None)
        last = items[-1]
        created = last.created_at or datetime.now(timezone.utc)
        return Page(items, encode_cursor("s", created.isoformat(), str(last.id)))

    def purge_expired_pending(self, newsletter_id: int) -> int:
        cutoff = _fmt_dt(datetime.now(timezone.utc) - CONFIRM_TTL)
        rows = self._get(
            "subscribers",
            {
                "newsletter_id": f"eq.{newsletter_id}",
                "confirmed_at": "is.null",
                "or": f"(created_at.is.null,created_at.lt.{cutoff})",
                "select": "id",
            },
        )
        for row in rows:
            self._delete("subscribers", {"id": row["id"]})
        return len(rows)

    def unsubscribe(self, token: str) -> None:
        self._patch("subscribers", {"token": token}, {"unsubscribed": True})

    def delete_subscriber(self, subscriber_id: int) -> None:
        self._delete("subscribers", {"id": subscriber_id})

    # --- Audit log ---

    def log_event(
        self,
        event_type: str,
        details: str = "{}",
        newsletter_id: int | None = None,
    ) -> None:
        self._post(
            "audit_log",
            {
                "newsletter_id": newsletter_id,
                "event_type": event_type,
                "details": details,
                "created_at": _fmt_dt(datetime.now(timezone.utc)),
            },
        )

    # --- Subscriber lookup ---

    def find_subscriber_by_email(self, newsletter_id: int, email: str) -> Subscriber | None:
        self.purge_expired_pending(newsletter_id)
        rows = self._get(
            "subscribers",
            {
                "newsletter_id": f"eq.{newsletter_id}",
                "email": f"eq.{email}",
                "order": "id.desc",
                "limit": "1",
            },
        )
        if not rows:
            return None
        return self._row_to_subscriber(rows[0])

    def reset_confirmation(
        self,
        newsletter_id: int,
        subscriber_id: int,
        *,
        resubscribe: bool = False,
    ) -> Subscriber | None:
        rows = self._get(
            "subscribers",
            {"id": f"eq.{subscriber_id}", "newsletter_id": f"eq.{newsletter_id}", "limit": "1"},
        )
        if not rows:
            return None
        data: dict = {"confirmed_at": None, "created_at": _fmt_dt(datetime.now(timezone.utc))}
        if resubscribe:
            data["unsubscribed"] = False
        row = self._patch("subscribers", {"id": subscriber_id, "newsletter_id": newsletter_id}, data)
        return self._row_to_subscriber(row)

    def get_subscriber_by_email(self, newsletter_id: int, email: str) -> Subscriber | None:
        self.purge_expired_pending(newsletter_id)
        rows = self._get(
            "subscribers",
            {
                "newsletter_id": f"eq.{newsletter_id}",
                "email": f"eq.{email}",
                "unsubscribed": "eq.false",
            },
        )
        if not rows:
            return None
        return self._row_to_subscriber(rows[0])
