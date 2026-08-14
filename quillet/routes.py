import json
import secrets
from functools import lru_cache
from importlib import resources
from urllib.parse import urljoin

import markdown2
from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    jsonify,
    make_response,
    render_template,
    request,
    url_for,
)

from . import api_ops
from .auth import require_basic_auth
from .db import NewsletterRepository
from .email import EmailSender
from .models import Newsletter, Post, Subscriber

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


@lru_cache(maxsize=256)
def _render_post_html(body_md: str) -> str:
    return markdown2.markdown(
        body_md,
        extras=["fenced-code-blocks", "header-ids", "strike", "tables"],
    )


def _unsubscribe_url_template(bp_name: str, newsletter_slug: str) -> str:
    base = _base_url()
    path = url_for(f"{bp_name}.unsubscribe", newsletter_slug=newsletter_slug, token="TOKEN_PLACEHOLDER")
    return urljoin(base, path).replace("TOKEN_PLACEHOLDER", "{token}")


def _post_url(bp_name: str, newsletter_slug: str, post_slug: str) -> str:
    base = _base_url()
    path = url_for(f"{bp_name}.post_detail", newsletter_slug=newsletter_slug, post_slug=post_slug)
    return urljoin(base, path)


def _post_list_url(bp_name: str, newsletter_slug: str) -> str:
    base = _base_url()
    path = url_for(f"{bp_name}.post_list", newsletter_slug=newsletter_slug)
    return urljoin(base, path)


def _confirm_url(bp_name: str, newsletter_slug: str, token: str) -> str:
    base = _base_url()
    path = url_for(f"{bp_name}.confirm_subscription", newsletter_slug=newsletter_slug, token=token)
    return urljoin(base, path)


def _send_test_email(
    newsletter: Newsletter,
    post: Post,
    test_email: str,
    bp_name: str,
    newsletter_slug: str,
) -> None:
    test_subscriber = Subscriber(
        id=0,
        newsletter_id=newsletter.id,
        email=test_email,
        token="test",
        confirmed_at=None,
    )
    config = _db().get_newsletter_config(newsletter.id)
    prefix = f"[TEST] {config.subject_prefix or ''}".strip()
    test_config = config._replace(subject_prefix=prefix)
    _email().send_post(
        newsletter,
        post,
        [test_subscriber],
        _unsubscribe_url_template(bp_name, newsletter_slug),
        test_config,
        post_url=_post_url(bp_name, newsletter_slug, post.slug),
        post_list_url=_post_list_url(bp_name, newsletter_slug),
    )


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
            response = jsonify(
                newsletter=newsletter._asdict(),
                posts=[p._asdict() for p in posts],
            )
            response.headers["Cache-Control"] = "public, max-age=300"
            return response

        response = make_response(
            render_template(
                "quillet/post_list.html",
                newsletter=newsletter,
                posts=posts,
            )
        )
        response.headers["Cache-Control"] = "public, max-age=300"
        return response

    @bp.get("/<newsletter_slug>/posts/<post_slug>")
    def post_detail(newsletter_slug: str, post_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        post = _db().get_post(newsletter_slug, post_slug)
        if post is None or post.published_at is None:
            abort(404)

        if _wants_json():
            response = jsonify(newsletter=newsletter._asdict(), post=post._asdict())
            response.headers["Cache-Control"] = "public, max-age=600"
            return response

        response = make_response(
            render_template(
                "quillet/post_detail.html",
                newsletter=newsletter,
                post=post,
                post_html=_render_post_html(post.body_md),
            )
        )
        response.headers["Cache-Control"] = "public, max-age=600"
        return response

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
        existing = _db().get_subscriber_by_email(newsletter.id, email)
        config = _db().get_newsletter_config(newsletter.id)

        if existing is not None:
            if existing.confirmed_at is None:
                confirm_url = _confirm_url(name, newsletter_slug, existing.token)
                _email().send_confirmation(newsletter, existing, confirm_url, config)
                _db().log_event(
                    "subscribe_resend",
                    json.dumps({"email": email, "newsletter_slug": newsletter_slug}),
                    newsletter_id=newsletter.id,
                )
            # Already confirmed: silently succeed — don't reveal subscription status
        else:
            subscriber = _db().add_subscriber(newsletter_slug, email, token)
            confirm_url = _confirm_url(name, newsletter_slug, subscriber.token)
            _email().send_confirmation(newsletter, subscriber, confirm_url, config)
            _db().log_event(
                "subscribe",
                json.dumps({"email": email, "newsletter_slug": newsletter_slug}),
                newsletter_id=newsletter.id,
            )

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

        if subscriber:
            _db().log_event(
                "confirm",
                json.dumps({"email": subscriber.email, "newsletter_slug": newsletter_slug}),
                newsletter_id=newsletter.id,
            )

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

        _db().log_event(
            "unsubscribe",
            json.dumps({"newsletter_slug": newsletter_slug}),
            newsletter_id=newsletter.id,
        )

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
                _render_post_html(post.body_md),
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
        response = current_app.response_class(xml, mimetype="application/rss+xml")
        response.headers["Cache-Control"] = "public, max-age=600"
        return response

    # --- API admin routes (always JSON, always Basic Auth) ---

    @bp.get("/<newsletter_slug>/api/openapi.yaml")
    def api_openapi_spec(newsletter_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        spec = resources.files("quillet").joinpath("openapi.yaml").read_text(encoding="utf-8")
        return Response(spec, mimetype="application/yaml")

    @bp.get("/<newsletter_slug>/api/mcp.md")
    def api_mcp_bootstrap(newsletter_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        template = resources.files("quillet").joinpath("mcp.md").read_text(encoding="utf-8")
        body = template.format(
            base_url=_base_url().rstrip("/"),
            newsletter_slug=newsletter_slug,
        )
        return Response(body, mimetype="text/markdown; charset=utf-8")

    @bp.get("/<newsletter_slug>/api/posts")
    @require_basic_auth
    def api_list_posts(newsletter_slug: str):
        payload, status = api_ops.list_posts(_db(), newsletter_slug, status=request.args.get("status") or "all")
        return jsonify(payload), status

    @bp.get("/<newsletter_slug>/api/posts/<post_slug>")
    @require_basic_auth
    def api_get_post(newsletter_slug: str, post_slug: str):
        payload, status = api_ops.get_post(_db(), newsletter_slug, post_slug)
        return jsonify(payload), status

    @bp.post("/<newsletter_slug>/api/posts")
    @require_basic_auth
    def api_create_post(newsletter_slug: str):
        data = request.get_json(silent=True) or {}
        payload, status = api_ops.create_post(
            _db(),
            newsletter_slug,
            title=data.get("title") or "",
            slug=data.get("slug") or "",
            body_md=data.get("body_md") or "",
        )
        return jsonify(payload), status

    @bp.put("/<newsletter_slug>/api/posts/<post_slug>")
    @require_basic_auth
    def api_update_post(newsletter_slug: str, post_slug: str):
        data = request.get_json(silent=True) or {}
        payload, status = api_ops.update_post(
            _db(),
            newsletter_slug,
            post_slug,
            title=data.get("title") or "",
            body_md=data.get("body_md") or "",
            slug=data.get("slug") or "",
        )
        return jsonify(payload), status

    @bp.post("/<newsletter_slug>/api/posts/<post_slug>/publish")
    @require_basic_auth
    def api_publish_post(newsletter_slug: str, post_slug: str):
        payload, status = api_ops.publish_post(_db(), newsletter_slug, post_slug)
        return jsonify(payload), status

    @bp.post("/<newsletter_slug>/api/posts/<post_slug>/send-test")
    @require_basic_auth
    def api_send_test_post(newsletter_slug: str, post_slug: str):
        data = request.get_json(silent=True) or {}
        payload, status = api_ops.send_test(
            current_app._get_current_object(),
            newsletter_slug,
            post_slug,
            email=data.get("email") or "",
        )
        return jsonify(payload), status

    @bp.post("/<newsletter_slug>/api/posts/<post_slug>/send")
    @require_basic_auth
    def api_send_post(newsletter_slug: str, post_slug: str):
        payload, status = api_ops.send_post(
            current_app._get_current_object(),
            newsletter_slug,
            post_slug,
        )
        return jsonify(payload), status

    @bp.get("/<newsletter_slug>/api/subscribers")
    @require_basic_auth
    def api_list_subscribers(newsletter_slug: str):
        payload, status = api_ops.list_subscribers(_db(), newsletter_slug)
        return jsonify(payload), status

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
