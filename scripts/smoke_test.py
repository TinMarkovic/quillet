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

from flask import Flask

from quillet import create_blueprint
from quillet.db.sqlalchemy import SQLAlchemyRepository


class _NoopSender:
    """Email sender that silently discards all messages."""

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

    app = Flask(__name__)
    app.register_blueprint(
        create_blueprint(
            db=db,
            email=_NoopSender(),
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
        subscribers = db.list_all_subscribers("blog")
        token = subscribers[0].token
        r = client.get(f"/newsletter/blog/confirm/{token}")
        failures += not _check("confirm subscription", r.status_code, 200)
        failures += not _check(
            "invalid token → 200 (renders invalid state)",
            client.get("/newsletter/blog/confirm/bad-token").status_code,
            200,
        )
        failures += not _check("unsubscribe", client.get(f"/newsletter/blog/unsubscribe/{token}").status_code, 200)

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

        failures += not _check(
            "publish", client.post("/newsletter/blog/api/posts/hello-world/publish", headers=auth).status_code, 200
        )
        failures += not _check("public post detail", client.get("/newsletter/blog/posts/hello-world").status_code, 200)
        failures += not _check("archive has post", client.get("/newsletter/blog/").status_code, 200)

        # re-subscribe so send has a confirmed subscriber
        client.post("/newsletter/blog/subscribe", data={"email": "reader2@example.com"})
        token2 = db.list_all_subscribers("blog")[-1].token
        db.confirm_subscriber(token2)

        r = client.post("/newsletter/blog/api/posts/hello-world/send", headers=auth)
        failures += not _check("send post", r.status_code, 200)
        failures += not _check(
            "double send → 409",
            client.post("/newsletter/blog/api/posts/hello-world/send", headers=auth).status_code,
            409,
        )

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

    print(f"\n{'All checks passed.' if not failures else f'{failures} check(s) FAILED.'}")
    return failures


if __name__ == "__main__":
    sys.exit(run())
