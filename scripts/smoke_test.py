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
from quillet.email._utils import apply_send_section, render_invite_md
from quillet.models import CONFIRM_TTL, is_pending_expired
from quillet.robots import register_robots, robots_txt


class _NoopSender:
    """Email sender that silently discards all messages."""

    def __init__(self) -> None:
        self.last_send_count = 0
        self.confirmations = 0
        self.last_confirm_body: str | None = None
        self.last_section_md = ""
        self.last_section_position = "append"

    def send_confirmation(self, newsletter, subscriber, confirm_url, config=None, body_md=None):
        self.confirmations += 1
        self.last_confirm_body = body_md
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
        section_md="",
        section_position="append",
    ):
        self.last_send_count = len(subscribers)
        self.last_section_md = section_md
        self.last_section_position = section_position
        print(f"  [email] post '{post.title}' → {len(subscribers)} subscriber(s)")


def _tied_second_ids(db: SQLAlchemyRepository) -> list[int]:
    newsletter = db.get_newsletter("blog")
    assert newsletter is not None
    ids: list[int] = []
    with db._engine.begin() as conn:
        for index in range(24):
            result = conn.execute(
                text(
                    "INSERT INTO subscribers (newsletter_id, email, token, unsubscribed, confirmed_at, created_at) "
                    "VALUES (:nid, :email, :token, 0, '2026-08-14 14:51:47', '2026-08-14 14:51:47')"
                ),
                {"nid": newsletter.id, "email": f"tied-{index}@example.com", "token": f"tied-token-{index}"},
            )
            ids.append(result.lastrowid)
        result = conn.execute(
            text(
                "INSERT INTO subscribers (newsletter_id, email, token, unsubscribed, confirmed_at, created_at) "
                "VALUES (:nid, 'tied-frac@example.com', 'tied-token-frac', 0, "
                "'2026-08-14 14:51:47.500000', '2026-08-14 14:51:47.500000')"
            ),
            {"nid": newsletter.id},
        )
        ids.append(result.lastrowid)
    return ids


def _walk_subscriber_ids(db: SQLAlchemyRepository) -> tuple[list[int], str | None]:
    seen: list[int] = []
    cursors: list[str] = []
    cursor = None
    for _ in range(12):
        page = db.list_all_subscribers("blog", limit=20, before=cursor)
        if seen and page.items and page.items[0].id == seen[-1]:
            return seen, "page repeated the previous last id"
        seen.extend(sub.id for sub in page.items)
        if page.next_cursor is None:
            return seen, None
        if page.next_cursor in cursors:
            return seen, "cursor repeated"
        cursors.append(page.next_cursor)
        cursor = page.next_cursor
    return seen, "walk did not finish"


def _auth_header(password: str = "secret") -> dict:
    creds = base64.b64encode(f"admin:{password}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


def _check(label: str, got: int, expected: int) -> bool:
    ok = got == expected
    status = "OK" if ok else "FAIL"
    print(f"  [{status}] {label}: HTTP {got}" + ("" if ok else f" (expected {expected})"))
    return ok


def _invite_checks(client, db: SQLAlchemyRepository, sender: _NoopSender, auth: dict) -> int:
    failures = 0
    print("\n-- Invite / reset --")
    before = sender.confirmations
    response = client.post(
        "/newsletter/blog/api/subscribers/invite",
        json={"email": "met@example.com", "body_md": "Good to meet you."},
        headers=auth,
    )
    failures += not _check("invite new subscriber", response.status_code, 201)
    met = db.find_subscriber_by_email(db.get_newsletter("blog").id, "met@example.com")
    if (
        met is None
        or met.confirmed_at is not None
        or sender.confirmations != before + 1
        or sender.last_confirm_body != "Good to meet you."
    ):
        failures += 1
        print("  [FAIL] invite should create one pending row and send the blurb")

    response = client.post(
        "/newsletter/blog/api/subscribers/invite",
        json={"email": "met@example.com", "body_md": "Still there."},
        headers=auth,
    )
    failures += not _check("invite pending resend", response.status_code, 200)
    rows = [sub for sub in _all_subscribers(db) if sub.email == "met@example.com"]
    if len(rows) != 1:
        failures += 1
        print(f"  [FAIL] invite resend should keep one row, got {len(rows)}")

    assert met is not None
    client.post(f"/newsletter/blog/confirm/{met.token}")
    confirmed = db.get_subscriber_by_token(met.token)
    assert confirmed is not None and confirmed.confirmed_at is not None
    response = client.post(
        "/newsletter/blog/api/subscribers/invite",
        json={"email": "met@example.com", "body_md": "Again."},
        headers=auth,
    )
    failures += not _check("invite already confirmed → 409", response.status_code, 409)
    if response.is_json and response.json.get("error") != "Already confirmed. Pass reset to reconfirm.":
        failures += 1
        print("  [FAIL] 409 body should name reset")
    still = db.get_subscriber_by_token(met.token)
    if still is None or still.confirmed_at != confirmed.confirmed_at:
        failures += 1
        print("  [FAIL] 409 invite must leave confirmation in place")

    admin = client.post(
        "/newsletter/blog/admin/subscribers/invite",
        data={"email": "met@example.com", "body_md": "From the form."},
        headers=auth,
    )
    failures += not _check("admin invite already confirmed → 409", admin.status_code, 409)
    if b"Already confirmed" not in admin.data:
        failures += 1
        print("  [FAIL] admin invite error should render on the subscriber page")

    token = met.token
    mailed = sender.confirmations
    response = client.post(
        "/newsletter/blog/api/subscribers/invite",
        json={"email": "met@example.com", "body_md": "One more time.", "reset": True},
        headers=auth,
    )
    failures += not _check("invite reset reconfirm", response.status_code, 200)
    reconfirmed = db.get_subscriber_by_token(token)
    if (
        reconfirmed is None
        or reconfirmed.confirmed_at is not None
        or reconfirmed.token != token
        or sender.last_confirm_body != "One more time."
        or sender.confirmations != mailed + 1
    ):
        failures += 1
        print("  [FAIL] reset invite should email, keep the token, and leave the row pending")

    client.post(f"/newsletter/blog/confirm/{token}")
    with db._engine.begin() as conn:
        conn.execute(
            text("UPDATE subscribers SET created_at = '2020-01-01 00:00:00' WHERE id = :id"),
            {"id": met.id},
        )
    mailed = sender.confirmations
    response = client.post(f"/newsletter/blog/api/subscribers/{met.id}/reset", headers=auth)
    failures += not _check("reset confirmation leaves pending", response.status_code, 200)
    reset_row = db.get_subscriber_by_token(token)
    if (
        reset_row is None
        or reset_row.confirmed_at is not None
        or reset_row.token != token
        or reset_row.created_at is None
        or reset_row.created_at.year == 2020
        or sender.confirmations != mailed
    ):
        failures += 1
        print("  [FAIL] reset should clear confirmation, keep the token, refresh created_at, and not email")

    preview = client.get(f"/newsletter/blog/confirm/{token}")
    after_preview = db.get_subscriber_by_token(token)
    if preview.status_code != 200 or b"Yes, subscribe" not in preview.data:
        failures += 1
        print("  [FAIL] confirm GET after reset should show the form")
    if after_preview is None or after_preview.confirmed_at is not None:
        failures += 1
        print("  [FAIL] confirm GET after reset must not confirm")

    db.unsubscribe(token)
    response = client.post(
        "/newsletter/blog/api/subscribers/invite",
        json={"email": "met@example.com", "body_md": "Welcome back."},
        headers=auth,
    )
    failures += not _check("invite unsubscribed resubscribes", response.status_code, 200)
    returned = db.get_subscriber_by_token(token)
    if returned is None or returned.unsubscribed or returned.confirmed_at is not None:
        failures += 1
        print("  [FAIL] invite of an unsubscribed address should clear unsubscribed and stay pending")

    other = db.create_newsletter("other", "Other", "o@example.com", "Other", None)
    foreign = db.add_subscriber("other", "foreign@example.com", "foreign-token")
    db.confirm_subscriber(foreign.token)
    foreign_stamp = db.get_subscriber_by_token("foreign-token").confirmed_at
    response = client.post(f"/newsletter/blog/api/subscribers/{foreign.id}/reset", headers=auth)
    failures += not _check("reset other newsletter → 404", response.status_code, 404)
    foreign_after = db.get_subscriber_by_token("foreign-token")
    if foreign_after is None or foreign_after.confirmed_at != foreign_stamp:
        failures += 1
        print("  [FAIL] foreign reset must not change the other newsletter")
    if other.id == db.get_newsletter("blog").id:
        failures += 1
        print("  [FAIL] other newsletter collided with blog")
    return failures


def _all_subscribers(db: SQLAlchemyRepository) -> list:
    rows = []
    cursor = None
    for _ in range(12):
        page = db.list_all_subscribers("blog", limit=100, before=cursor)
        rows.extend(page.items)
        if page.next_cursor is None:
            return rows
        cursor = page.next_cursor
    return rows


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
        failures += not _check("confirm GET does not confirm", r.status_code, 200)
        if b"Yes, subscribe" not in r.data:
            failures += 1
            print("  [FAIL] confirm GET: expected confirm form")
        pending = db.get_subscriber_by_token(token)
        if pending is None or pending.confirmed_at is not None:
            failures += 1
            print("  [FAIL] confirm GET must not set confirmed_at")
        r = client.get(
            f"/newsletter/blog/confirm/{token}",
            headers={"Accept": "application/json"},
        )
        failures += not _check("confirm JSON GET → 405", r.status_code, 405)
        r = client.post(f"/newsletter/blog/confirm/{token}")
        failures += not _check("confirm POST confirms", r.status_code, 200)
        confirmed_reader = db.get_subscriber_by_token(token)
        if confirmed_reader is None or confirmed_reader.confirmed_at is None:
            failures += 1
            print("  [FAIL] confirm POST must set confirmed_at")
        else:
            stamp = confirmed_reader.confirmed_at
            client.post(f"/newsletter/blog/confirm/{token}")
            again = db.get_subscriber_by_token(token)
            if again is None or again.confirmed_at != stamp:
                failures += 1
                print("  [FAIL] second confirm POST must not rewrite confirmed_at")
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

        r = client.post(
            "/newsletter/blog/api/posts/hello-world/send",
            json={"section_md": "SECTION-MARKER", "section_position": "beside"},
            headers=auth,
        )
        failures += not _check("send section bad position → 400", r.status_code, 400)
        r = client.get("/newsletter/blog/api/posts/hello-world", headers=auth)
        if r.is_json and r.json.get("post", {}).get("sent_at") is not None:
            failures += 1
            print("  [FAIL] rejected section must not set sent_at")

        r = client.post(
            "/newsletter/blog/api/posts/hello-world/send",
            json={"section_md": "SECTION-MARKER", "section_position": "append"},
            headers=auth,
        )
        failures += not _check("send post", r.status_code, 200)
        stored = client.get("/newsletter/blog/api/posts/hello-world", headers=auth)
        public = client.get("/newsletter/blog/posts/hello-world")
        stored_body = ""
        if stored.is_json:
            stored_body = stored.json.get("post", {}).get("body_md") or ""
        section_kept_off_post = (
            sender.last_section_md == "SECTION-MARKER"
            and sender.last_section_position == "append"
            and "SECTION-MARKER" not in stored_body
            and b"SECTION-MARKER" not in public.data
        )
        if not section_kept_off_post:
            failures += 1
            print("  [FAIL] section marker leaked onto the post or was not passed to the sender")
        failures += not _check("send section is not stored on the post", 200 if section_kept_off_post else 0, 200)

        prepended = apply_send_section("# Title\n\nbody", "SECTION-MARKER", "prepend")
        appended = apply_send_section("# Title\n\nbody", "SECTION-MARKER", "append")
        if not prepended.startswith("SECTION-MARKER") or not appended.endswith("SECTION-MARKER"):
            failures += 1
            print("  [FAIL] apply_send_section placement")
        invite_md = render_invite_md("Good to meet you.", "Test Blog", "https://example.test/confirm/abc")
        if invite_md is None or "https://example.test/confirm/abc" not in invite_md:
            failures += 1
            print("  [FAIL] render_invite_md should append the confirm link")

        r = client.post(
            "/newsletter/blog/api/posts/hello-world/send-test",
            json={"email": "owner@example.com", "section_md": "PREPEND-MARKER", "section_position": "prepend"},
            headers=auth,
        )
        failures += not _check("send-test section prepend", r.status_code, 200)
        if sender.last_section_md != "PREPEND-MARKER" or sender.last_section_position != "prepend":
            failures += 1
            print("  [FAIL] send-test did not record prepend section")
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

        if not is_pending_expired(None, None):
            failures += 1
            print("  [FAIL] pending with no created_at should be expired")

        client.post("/newsletter/blog/subscribe", data={"email": "null-created@example.com"})
        null_pending = next(s for s in db.list_all_subscribers("blog").items if s.email == "null-created@example.com")
        with db._engine.begin() as conn:
            conn.execute(
                text("UPDATE subscribers SET created_at = NULL WHERE id = :id"),
                {"id": null_pending.id},
            )
        db.list_all_subscribers("blog")
        if db.get_subscriber_by_token(null_pending.token) is not None:
            failures += 1
            print("  [FAIL] null created_at pending should be purged on list")

        confirmed = next(s for s in db.list_all_subscribers("blog").items if s.email == "reader2@example.com")
        with db._engine.begin() as conn:
            conn.execute(
                text("UPDATE subscribers SET created_at = NULL WHERE id = :id"),
                {"id": confirmed.id},
            )
        db.list_all_subscribers("blog")
        if db.get_subscriber_by_token(confirmed.token) is None:
            failures += 1
            print("  [FAIL] confirmed subscriber must not be purged when created_at is null")

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

        tied_ids = _tied_second_ids(db)
        seen_ids, walk_error = _walk_subscriber_ids(db)
        tied_seen = [item for item in seen_ids if item in set(tied_ids)]
        tied_ok = walk_error is None and len(tied_seen) == len(tied_ids) and len(set(tied_seen)) == len(tied_ids)
        if not tied_ok:
            print(f"  [FAIL] tied-second walk: {walk_error} got {len(set(tied_seen))} of {len(tied_ids)}")
        failures += not _check("sqlite tied-second page walks every id once", 200 if tied_ok else 0, 200)

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

        failures += _invite_checks(client, db, sender, auth)

    print(f"\n{'All checks passed.' if not failures else f'{failures} check(s) FAILED.'}")
    return failures


if __name__ == "__main__":
    sys.exit(run())
