#!/usr/bin/env python3
"""
smoke_test.py — end-to-end smoke test for QUILLET in Blueprint mode.

Run from the repo root with the virtualenv active:
    python scripts/smoke_test.py

Uses an in-memory SQLite DB and a no-op email sender, so no real DB file
or email service is needed.
"""

import base64
import sys
from datetime import datetime, timedelta, timezone

from flask import Flask
from sqlalchemy import text

from quillet import create_blueprint
from quillet.db.sqlalchemy import SQLAlchemyRepository
from quillet.models import CONFIRM_TTL
from quillet.robots import register_robots, robots_txt


class _NoopSender:
    """Email sender that silently discards all messages."""

    def __init__(self) -> None:
        self.last_send_count = 0

    def send_confirmation(self, newsletter, subscriber, confirm_url, config=None):
        print(f"  [email] confirmation → {subscriber.email}  url={confirm_url}")

    def send_post(
        self,
        newsletter,
        post,
        subscribers,
        unsubscribe_url_template,
        config=None,
        post_url="",
        post_list_url="",
    ):
        self.last_send_count = len(subscribers)
        print(f"  [email] post '{post.title}' → {len(subscribers)} subscriber(s)")


def _auth_header(password: str = "secret") -> dict:
    creds = base64.b64encode(f"admin:{password}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


def _check(label: str, got: int, expected: int) -> bool:
    ok = got == expected
    status = "OK" if ok else "FAIL"
    print(f"  [{status}] {label}: HTTP {got}" + ("" if ok else f" (expected {expected})"))
    return ok


def run() -> int:
    db = SQLAlchemyRepository("sqlite:///:memory:")
    db.create_newsletter(
        slug="blog",
        name="Test Blog",
        from_email="hi@example.com",
        from_name="Test Author",
        reply_to=None,
    )

    sender = _NoopSender()
    app = Flask(__name__)
    app.register_blueprint(
        create_blueprint(
            db=db,
            email=sender,
            admin_password="secret",
        ),
        url_prefix="/newsletter",
    )

    failures = 0

    with app.test_client() as client:
        auth = _auth_header()

        print("\n-- Public routes --")
        failures += not _check("archive (empty)", client.get("/newsletter/blog/").status_code, 200)
        failures += not _check("unknown newsletter 404", client.get("/newsletter/nope/").status_code, 404)

        print("\n-- Subscribe flow --")
        r = client.post("/newsletter/blog/subscribe", data={"email": "reader@example.com"})
        failures += not _check("subscribe", r.status_code, 200)

        # grab the token from the DB directly to simulate email click
        subscribers = db.list_all_subscribers("blog").items
        token = subscribers[0].token
        r = client.get(f"/newsletter/blog/confirm/{token}")
        failures += not _check("confirm subscription", r.status_code, 200)
        failures += not _check(
            "invalid token → 200 (renders invalid state)",
            client.get("/newsletter/blog/confirm/bad-token").status_code,
            200,
        )
        r = client.get(f"/newsletter/blog/unsubscribe/{token}")
        failures += not _check("unsubscribe GET form", r.status_code, 200)
        if b"Unsubscribe?" not in r.data and b"unsubscribe" not in r.data.lower():
            failures += 1
            print("  [FAIL] unsubscribe GET: expected confirm form")
        still = db.get_subscriber_by_token(token)
        if still is None or still.unsubscribed:
            failures += 1
            print("  [FAIL] unsubscribe GET must not change subscriber")
        r = client.get(
            f"/newsletter/blog/unsubscribe/{token}",
            headers={"Accept": "application/json"},
        )
        failures += not _check("unsubscribe JSON GET → 405", r.status_code, 405)
        r = client.post(f"/newsletter/blog/unsubscribe/{token}")
        failures += not _check("unsubscribe POST", r.status_code, 200)
        gone = db.get_subscriber_by_token(token)
        if gone is None or not gone.unsubscribed:
            failures += 1
            print("  [FAIL] unsubscribe POST must set unsubscribed")

        print("\n-- Admin auth --")
        failures += not _check("dashboard no auth → 401", client.get("/newsletter/blog/admin/").status_code, 401)
        failures += not _check(
            "dashboard wrong password → 401",
            client.get("/newsletter/blog/admin/", headers=_auth_header("wrong")).status_code,
            401,
        )
        failures += not _check(
            "dashboard with auth → 200", client.get("/newsletter/blog/admin/", headers=auth).status_code, 200
        )

        print("\n-- Post lifecycle (API) --")
        r = client.post(
            "/newsletter/blog/api/posts",
            json={"title": "Hello World", "slug": "hello-world", "body_md": "# Hello\n\nFirst post."},
            headers=auth,
        )
        failures += not _check("create post", r.status_code, 201)

        r = client.get("/newsletter/blog/api/posts?status=draft", headers=auth)
        failures += not _check("list drafts", r.status_code, 200)
        if r.is_json:
            draft_slugs = {p["slug"] for p in r.json.get("posts", [])}
            failures += not _check("list drafts includes hello-world", 200 if "hello-world" in draft_slugs else 0, 200)
        else:
            failures += 1
            print("  [FAIL] list drafts: expected JSON")

        r = client.get("/newsletter/blog/api/posts/hello-world", headers=auth)
        failures += not _check("get draft via API", r.status_code, 200)
        if r.is_json and r.json.get("post", {}).get("published_at") is not None:
            failures += 1
            print("  [FAIL] get draft via API: expected published_at null")

        r = client.put(
            "/newsletter/blog/api/posts/hello-world",
            json={"title": "Hello World (edited)", "body_md": "# Hello\n\nEdited."},
            headers=auth,
        )
        failures += not _check("update post", r.status_code, 200)
        if r.is_json:
            updated = r.json.get("post", {})
            if updated.get("title") != "Hello World (edited)" or updated.get("slug") != "hello-world":
                failures += 1
                print("  [FAIL] update post: title/slug mismatch")

        failures += not _check(
            "publish", client.post("/newsletter/blog/api/posts/hello-world/publish", headers=auth).status_code, 200
        )
        failures += not _check("public post detail", client.get("/newsletter/blog/posts/hello-world").status_code, 200)
        failures += not _check("archive has post", client.get("/newsletter/blog/").status_code, 200)

        r = client.post(
            "/newsletter/blog/api/posts/hello-world/send-test",
            json={"email": "owner@example.com"},
            headers=auth,
        )
        failures += not _check("send-test via API", r.status_code, 200)
        r = client.get("/newsletter/blog/api/posts/hello-world", headers=auth)
        if r.is_json and r.json.get("post", {}).get("sent_at") is not None:
            failures += 1
            print("  [FAIL] send-test must not set sent_at")

        # re-subscribe so send has a confirmed subscriber
        client.post("/newsletter/blog/subscribe", data={"email": "reader2@example.com"})
        reader2 = next(s for s in db.list_all_subscribers("blog").items if s.email == "reader2@example.com")
        db.confirm_subscriber(reader2.token)

        r = client.post("/newsletter/blog/api/posts/hello-world/send", headers=auth)
        failures += not _check("send post", r.status_code, 200)
        if sender.last_send_count != 1:
            failures += 1
            print(f"  [FAIL] send post should reach 1 confirmed, got {sender.last_send_count}")
        failures += not _check(
            "double send → 409",
            client.post("/newsletter/blog/api/posts/hello-world/send", headers=auth).status_code,
            409,
        )

        r = client.get("/newsletter/blog/api/openapi.yaml")
        failures += not _check("openapi yaml", r.status_code, 200)
        if b"openapi:" not in r.data[:64]:
            failures += 1
            print("  [FAIL] openapi yaml: missing openapi: header")

        r = client.get("/newsletter/blog/api/mcp.md")
        failures += not _check("mcp bootstrap md", r.status_code, 200)
        if b".cursor/mcp.json" not in r.data or b'"url"' not in r.data or b"/mcp/" not in r.data:
            failures += 1
            print("  [FAIL] mcp bootstrap md: missing expected URL-based bootstrap content")

        print("\n-- Admin UI --")
        failures += not _check(
            "new post form", client.get("/newsletter/blog/admin/posts/new", headers=auth).status_code, 200
        )
        failures += not _check(
            "edit post form", client.get("/newsletter/blog/admin/posts/hello-world/edit", headers=auth).status_code, 200
        )
        failures += not _check(
            "subscriber list", client.get("/newsletter/blog/admin/subscribers", headers=auth).status_code, 200
        )
        failures += not _check(
            "settings page", client.get("/newsletter/blog/admin/settings", headers=auth).status_code, 200
        )
        failures += not _check(
            "send-test",
            client.post(
                "/newsletter/blog/admin/posts/hello-world/send-test",
                data={"test_email": "owner@example.com"},
                headers=auth,
            ).status_code,
            302,
        )

        print("\n-- JSON accept header --")
        r = client.get("/newsletter/blog/", headers={**auth, "Accept": "application/json"})
        failures += not _check("archive JSON", r.status_code, 200)
        assert r.is_json, "expected JSON response"
        assert "posts" in r.json, "expected 'posts' key"

        print("\n-- Expiry / pagination / discovery --")
        client.post("/newsletter/blog/subscribe", data={"email": "stale@example.com"})
        stale = next(s for s in db.list_all_subscribers("blog").items if s.email == "stale@example.com")
        old = datetime.now(timezone.utc) - CONFIRM_TTL - timedelta(hours=1)
        with db._engine.begin() as conn:
            conn.execute(
                text("UPDATE subscribers SET created_at = :created WHERE id = :id"),
                {"created": old.replace(tzinfo=None), "id": stale.id},
            )
        r = client.get(f"/newsletter/blog/confirm/{stale.token}")
        failures += not _check("expired confirm still 200 (invalid)", r.status_code, 200)
        if db.get_subscriber_by_token(stale.token) is not None:
            failures += 1
            print("  [FAIL] expired pending should be deleted on confirm")

        for i in range(21):
            post = db.create_post("blog", f"P{i}", f"p-{i}", f"# P{i}\n\nbody")
            db.publish_post(post.id)
        r = client.get("/newsletter/blog/", headers={"Accept": "application/json"})
        failures += not _check("paged archive JSON", r.status_code, 200)
        posts_page = r.json.get("posts", [])
        cursor = r.json.get("next_cursor")
        if len(posts_page) != 20 or not cursor:
            failures += 1
            print(f"  [FAIL] expected 20 posts and next_cursor, got {len(posts_page)} cursor={cursor!r}")
        r2 = client.get(f"/newsletter/blog/?cursor={cursor}", headers={"Accept": "application/json"})
        failures += not _check("archive page 2", r2.status_code, 200)
        if r2.is_json and len(r2.json.get("posts", [])) < 1:
            failures += 1
            print("  [FAIL] page 2 should have remaining posts")

        r = client.get("/newsletter/blog/api/posts", headers=auth)
        failures += not _check("api posts page", r.status_code, 200)
        api_cursor = r.json.get("next_cursor") if r.is_json else None
        if not r.is_json or len(r.json.get("posts", [])) != 20 or not api_cursor:
            failures += 1
            print("  [FAIL] /api/posts should page at 20 with next_cursor")
        r = client.get(f"/newsletter/blog/api/posts?cursor={api_cursor}", headers=auth)
        failures += not _check("api posts page 2", r.status_code, 200)
        r = client.get("/newsletter/blog/?cursor=not-a-cursor")
        failures += not _check("invalid cursor → 400", r.status_code, 400)

        r = client.get("/newsletter/blog/posts/hello-world", headers={"Accept": "text/markdown"})
        failures += not _check("markdown post", r.status_code, 200)
        if b"# Hello World" not in r.data:
            failures += 1
            print("  [FAIL] markdown post missing title")

        r = client.get("/newsletter/blog/posts/hello-world")
        if b"application/ld+json" not in r.data:
            failures += 1
            print("  [FAIL] post HTML missing JSON-LD")

        r = client.get("/newsletter/blog/sitemap.xml")
        failures += not _check("sitemap", r.status_code, 200)
        if b"hello-world" not in r.data:
            failures += 1
            print("  [FAIL] sitemap missing published post")

        r = client.get("/newsletter/blog/llms.txt")
        failures += not _check("llms.txt", r.status_code, 200)
        if b"hello-world" not in r.data:
            failures += 1
            print("  [FAIL] llms.txt missing published post")

        r = client.post(
            "/newsletter/blog/admin/settings",
            data={
                "name": "Test Blog",
                "from_name": "Test Author",
                "from_email": "hi@example.com",
                "subscribe_position": "start",
                "public_description": "Hello archive.",
            },
            headers=auth,
        )
        failures += not _check("save settings", r.status_code, 302)
        r = client.get("/newsletter/blog/")
        if b"Hello archive." not in r.data:
            failures += 1
            print("  [FAIL] archive missing public_description")

        body = robots_txt(db, url_prefix="/newsletter", base_url="http://example.com")
        if (
            "Disallow: /newsletter/*/admin" not in body
            or "Sitemap: http://example.com/newsletter/blog/sitemap.xml" not in body
        ):
            failures += 1
            print("  [FAIL] robots_txt contents")
            print(body)

        robots_app = Flask("robots")
        register_robots(robots_app, db, url_prefix="/newsletter", base_url="http://example.com")
        with robots_app.test_client() as robots_client:
            r = robots_client.get("/robots.txt")
            failures += not _check("GET /robots.txt", r.status_code, 200)

        confirmed = db.list_confirmed_subscribers("blog")
        fanout_post = db.create_post("blog", "Fanout", "fanout", "# Fanout\n\nall")
        db.publish_post(fanout_post.id)
        r = client.post("/newsletter/blog/api/posts/fanout/send", headers=auth)
        failures += not _check("send fanout", r.status_code, 200)
        if not confirmed or sender.last_send_count != len(confirmed):
            failures += 1
            print(f"  [FAIL] send should reach all {len(confirmed)} confirmed, got {sender.last_send_count}")

    print(f"\n{'All checks passed.' if not failures else f'{failures} check(s) FAILED.'}")
    return failures


if __name__ == "__main__":
    sys.exit(run())
