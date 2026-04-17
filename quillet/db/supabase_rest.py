from datetime import datetime, timezone

import requests

from ..models import Newsletter, Post, Subscriber

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
    (same schema as SQLAlchemyRepository).
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

    # --- Posts ---

    def list_posts(self, newsletter_slug: str, published_only: bool = True) -> list[Post]:
        newsletter_id = self._get_newsletter_id(newsletter_slug)
        if newsletter_id is None:
            return []
        params: dict = {"newsletter_id": f"eq.{newsletter_id}", "order": "published_at.desc"}
        if published_only:
            params["published_at"] = "not.is.null"
        rows = self._get("posts", params)
        return [self._row_to_post(r) for r in rows]

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
        row = self._post(
            "subscribers",
            {"newsletter_id": newsletter_id, "email": email, "token": token, "unsubscribed": False},
        )
        return self._row_to_subscriber(row)

    def confirm_subscriber(self, token: str) -> Subscriber | None:
        rows = self._get("subscribers", {"token": f"eq.{token}"})
        if not rows:
            return None
        now = _fmt_dt(datetime.now(timezone.utc))
        row = self._patch("subscribers", {"token": token}, {"confirmed_at": now})
        return self._row_to_subscriber(row)

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

    def list_all_subscribers(self, newsletter_slug: str) -> list[Subscriber]:
        newsletter_id = self._get_newsletter_id(newsletter_slug)
        if newsletter_id is None:
            return []
        rows = self._get(
            "subscribers",
            {"newsletter_id": f"eq.{newsletter_id}"},
        )
        return [self._row_to_subscriber(r) for r in rows]

    def unsubscribe(self, token: str) -> None:
        self._patch("subscribers", {"token": token}, {"unsubscribed": True})

    def delete_subscriber(self, subscriber_id: int) -> None:
        self._delete("subscribers", {"id": subscriber_id})
