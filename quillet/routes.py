import secrets
from urllib.parse import urljoin

import markdown2
from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    render_template,
    request,
    url_for,
)

from .auth import require_basic_auth
from .db import NewsletterRepository
from .email import EmailSender
from .models import Post

# ---------------------------------------------------------------------------
# Helpers (used by admin.py too)
# ---------------------------------------------------------------------------


def _db() -> NewsletterRepository:
    return current_app.config["QUILLET_DB"]


def _email() -> EmailSender:
    return current_app.config["QUILLET_EMAIL"]


def _base_url() -> str:
    return current_app.config.get("QUILLET_BASE_URL", request.host_url.rstrip("/"))


def _is_api_mode() -> bool:
    return current_app.config.get("QUILLET_MODE", "web") == "api"


def _wants_json() -> bool:
    return _is_api_mode() or request.accept_mimetypes.best == "application/json"


def _render_post_html(post: Post) -> str:
    return markdown2.markdown(
        post.body_md,
        extras=["header-ids", "strike", "metadata"],
    )


def _unsubscribe_url_template(bp_name: str, newsletter_slug: str) -> str:
    base = _base_url()
    path = url_for(f"{bp_name}.unsubscribe", newsletter_slug=newsletter_slug, token="TOKEN_PLACEHOLDER")
    return urljoin(base, path).replace("TOKEN_PLACEHOLDER", "{token}")


def _confirm_url(bp_name: str, newsletter_slug: str, token: str) -> str:
    base = _base_url()
    path = url_for(f"{bp_name}.confirm_subscription", newsletter_slug=newsletter_slug, token=token)
    return urljoin(base, path)


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def register_public_routes(bp: Blueprint) -> None:
    name = bp.name

    @bp.get("/<newsletter_slug>/")
    def post_list(newsletter_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        posts = _db().list_posts(newsletter_slug, published_only=True)

        if _wants_json():
            return jsonify(
                newsletter=newsletter._asdict(),
                posts=[p._asdict() for p in posts],
            )

        return render_template(
            "quillet/post_list.html",
            newsletter=newsletter,
            posts=posts,
        )

    @bp.get("/<newsletter_slug>/posts/<post_slug>")
    def post_detail(newsletter_slug: str, post_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        post = _db().get_post(newsletter_slug, post_slug)
        if post is None or post.published_at is None:
            abort(404)

        if _wants_json():
            return jsonify(newsletter=newsletter._asdict(), post=post._asdict())

        return render_template(
            "quillet/post_detail.html",
            newsletter=newsletter,
            post=post,
            post_html=_render_post_html(post),
        )

    @bp.post("/<newsletter_slug>/subscribe")
    def subscribe(newsletter_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        if request.is_json:
            email = (request.get_json(silent=True) or {}).get("email", "").strip()
        else:
            email = request.form.get("email", "").strip()

        if not email:
            if _wants_json():
                return jsonify(error="Email is required."), 400
            return (
                render_template(
                    "quillet/post_list.html",
                    newsletter=newsletter,
                    posts=_db().list_posts(newsletter_slug),
                    subscribe_error="Email is required.",
                ),
                400,
            )

        token = secrets.token_urlsafe(32)
        subscriber = _db().add_subscriber(newsletter_slug, email, token)
        confirm_url = _confirm_url(name, newsletter_slug, subscriber.token)
        config = _db().get_newsletter_config(newsletter.id)
        _email().send_confirmation(newsletter, subscriber, confirm_url, config)

        if _wants_json():
            return jsonify(ok=True, message="Confirmation email sent."), 201

        return render_template(
            "quillet/subscribe_confirm.html",
            newsletter=newsletter,
            state="pending",
        )

    @bp.get("/<newsletter_slug>/confirm/<token>")
    def confirm_subscription(newsletter_slug: str, token: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        subscriber = _db().confirm_subscriber(token)

        if _wants_json():
            if subscriber is None:
                return jsonify(error="Invalid or expired token."), 404
            return jsonify(ok=True)

        return render_template(
            "quillet/subscribe_confirm.html",
            newsletter=newsletter,
            state="confirmed" if subscriber else "invalid",
        )

    @bp.get("/<newsletter_slug>/unsubscribe/<token>")
    def unsubscribe(newsletter_slug: str, token: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        _db().unsubscribe(token)

        if _wants_json():
            return jsonify(ok=True)

        return render_template(
            "quillet/subscribe_confirm.html",
            newsletter=newsletter,
            state="unsubscribed",
        )

    @bp.get("/<newsletter_slug>/feed.xml")
    def feed(newsletter_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        posts = _db().list_posts(newsletter_slug, published_only=True)
        base = _base_url()

        items = [
            (
                post,
                _render_post_html(post),
                urljoin(base, url_for(f"{name}.post_detail", newsletter_slug=newsletter_slug, post_slug=post.slug)),
            )
            for post in posts
        ]

        channel_url = urljoin(base, url_for(f"{name}.post_list", newsletter_slug=newsletter_slug))
        feed_url = urljoin(base, url_for(f"{name}.feed", newsletter_slug=newsletter_slug))

        xml = render_template(
            "quillet/feed.xml",
            newsletter=newsletter,
            items=items,
            channel_url=channel_url,
            feed_url=feed_url,
        )
        return current_app.response_class(xml, mimetype="application/rss+xml")

    # --- API admin routes (always JSON, always Basic Auth) ---

    @bp.post("/<newsletter_slug>/api/posts")
    @require_basic_auth
    def api_create_post(newsletter_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()
        slug = (data.get("slug") or "").strip()
        body_md = (data.get("body_md") or "").strip()

        if not title or not slug or not body_md:
            return jsonify(error="title, slug, and body_md are required."), 400

        post = _db().create_post(newsletter_slug, title, slug, body_md)
        return jsonify(post=post._asdict()), 201

    @bp.post("/<newsletter_slug>/api/posts/<post_slug>/publish")
    @require_basic_auth
    def api_publish_post(newsletter_slug: str, post_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        post = _db().get_post(newsletter_slug, post_slug)
        if post is None:
            abort(404)

        updated = _db().publish_post(post.id)
        return jsonify(post=updated._asdict())

    @bp.post("/<newsletter_slug>/api/posts/<post_slug>/send")
    @require_basic_auth
    def api_send_post(newsletter_slug: str, post_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        post = _db().get_post(newsletter_slug, post_slug)
        if post is None:
            abort(404)
        if post.published_at is None:
            return jsonify(error="Post must be published before sending."), 400
        if post.sent_at is not None:
            return jsonify(error="Post has already been sent."), 409

        subscribers = _db().list_confirmed_subscribers(newsletter_slug)
        _email().send_post(
            newsletter,
            post,
            subscribers,
            _unsubscribe_url_template(name, newsletter_slug),
            _db().get_newsletter_config(newsletter.id),
        )
        _db().mark_sent(post.id)

        return jsonify(ok=True, recipients=len(subscribers))

    @bp.get("/<newsletter_slug>/api/subscribers")
    @require_basic_auth
    def api_list_subscribers(newsletter_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        subscribers = _db().list_all_subscribers(newsletter_slug)
        return jsonify(subscribers=[s._asdict() for s in subscribers])

    @bp.delete("/<newsletter_slug>/api/posts/<post_slug>")
    @require_basic_auth
    def api_delete_post(newsletter_slug: str, post_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        post = _db().get_post(newsletter_slug, post_slug)
        if post is None:
            abort(404)

        _db().delete_post(post.id)
        return jsonify(ok=True)

    @bp.delete("/<newsletter_slug>/api/subscribers/<int:subscriber_id>")
    @require_basic_auth
    def api_delete_subscriber(newsletter_slug: str, subscriber_id: int):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        _db().delete_subscriber(subscriber_id)
        return jsonify(ok=True)
