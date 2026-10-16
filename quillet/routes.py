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
from .models import (
    PAGE_MAX,
    PAGE_SIZE,
    RSS_LIMIT,
    InvalidCursor,
    Newsletter,
    Post,
    Subscriber,
    clamp_limit,
    is_pending_expired,
)

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
    return _response_format() == "json"


def _response_format() -> str:
    if _is_api_mode():
        return "json"
    best = request.accept_mimetypes.best_match(
        ["text/html", "application/json", "text/markdown", "text/plain"],
        default="text/html",
    )
    if best in ("text/markdown", "text/plain"):
        return "markdown"
    if best == "application/json":
        return "json"
    return "html"


def _list_args() -> tuple[int, str | None, bool]:
    raw_limit = request.args.get("limit")
    try:
        limit = clamp_limit(int(raw_limit)) if raw_limit else PAGE_SIZE
    except ValueError:
        abort(400)
    cursor = request.args.get("cursor") or None
    partial = request.args.get("partial") == "1"
    return limit, cursor, partial


def _iter_published_posts(newsletter_slug: str) -> list[Post]:
    items: list[Post] = []
    cursor = None
    while True:
        page = _db().list_posts(newsletter_slug, published_only=True, limit=PAGE_MAX, before=cursor)
        items.extend(page.items)
        if not page.next_cursor:
            return items
        cursor = page.next_cursor


def _vary(response: Response) -> Response:
    response.headers["Vary"] = "Accept"
    return response


@lru_cache(maxsize=256)
def _render_post_html(body_md: str) -> str:
    return markdown2.markdown(
        body_md,
        extras=["fenced-code-blocks", "header-ids", "strike", "tables"],
    )


def _unsubscribe_url_template(bp_name: str, newsletter_slug: str) -> str:
    base = _base_url()
    path = url_for(f"{bp_name}.unsubscribe_form", newsletter_slug=newsletter_slug, token="TOKEN_PLACEHOLDER")
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
    section_md: str = "",
    section_position: str = "append",
) -> None:
    test_subscriber = Subscriber(
        id=0,
        newsletter_id=newsletter.id,
        email=test_email,
        token="test",
        confirmed_at=None,
        created_at=None,
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
        section_md=section_md,
        section_position=section_position,
    )


def _confirm_state(subscriber: Subscriber | None) -> str:
    if subscriber is None or is_pending_expired(subscriber.created_at, subscriber.confirmed_at):
        return "invalid"
    if subscriber.confirmed_at is not None:
        return "confirmed"
    return "confirm"


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

        config = _db().get_newsletter_config(newsletter.id)
        limit, cursor, partial = _list_args()
        try:
            page = _db().list_posts(newsletter_slug, published_only=True, limit=limit, before=cursor)
        except InvalidCursor:
            abort(400)
        posts = page.items
        fmt = _response_format()

        if fmt == "json":
            response = jsonify(
                newsletter=newsletter._asdict(),
                posts=[p._asdict() for p in posts],
                next_cursor=page.next_cursor,
            )
            response.headers["Cache-Control"] = "public, max-age=300"
            return _vary(response)

        if fmt == "markdown":
            lines = [f"# {newsletter.name}", ""]
            for post in posts:
                date = post.published_at.strftime("%Y-%m-%d") if post.published_at else ""
                url = _post_url(name, newsletter_slug, post.slug)
                lines.append(f"- [{post.title}]({url}) — {date}".rstrip(" —"))
            if page.next_cursor:
                lines.append("")
                lines.append(f"next_cursor: {page.next_cursor}")
            response = Response("\n".join(lines) + "\n", mimetype="text/markdown; charset=utf-8")
            response.headers["Cache-Control"] = "public, max-age=300"
            return _vary(response)

        description_html = ""
        if config.public_description:
            description_html = _render_post_html(config.public_description)

        if partial:
            return render_template(
                "quillet/_post_list_items.html",
                newsletter=newsletter,
                posts=posts,
                next_cursor=page.next_cursor,
            )

        response = make_response(
            render_template(
                "quillet/post_list.html",
                newsletter=newsletter,
                posts=posts,
                next_cursor=page.next_cursor,
                partial=False,
                subscribe_position=config.subscribe_position or "end",
                description_html=description_html,
            )
        )
        response.headers["Cache-Control"] = "public, max-age=300"
        return _vary(response)

    @bp.get("/<newsletter_slug>/sitemap.xml")
    def sitemap(newsletter_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        posts = _iter_published_posts(newsletter_slug)
        base = _base_url()
        urls = [urljoin(base, url_for(f"{name}.post_list", newsletter_slug=newsletter_slug))]
        urls.extend(
            urljoin(base, url_for(f"{name}.post_detail", newsletter_slug=newsletter_slug, post_slug=post.slug))
            for post in posts
        )
        xml_urls = "".join(f"  <url><loc>{url}</loc></url>\n" for url in urls)
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{xml_urls}</urlset>\n"
        )
        response = current_app.response_class(xml, mimetype="application/xml")
        response.headers["Cache-Control"] = "public, max-age=600"
        return response

    @bp.get("/<newsletter_slug>/llms.txt")
    def llms_txt(newsletter_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        posts = _iter_published_posts(newsletter_slug)
        lines = [f"# {newsletter.name}", ""]
        if newsletter:
            lines.append(f"> {newsletter.name}")
            lines.append("")
        for post in posts:
            url = _post_url(name, newsletter_slug, post.slug)
            lines.append(f"- [{post.title}]({url}) (text/markdown)")
        response = Response("\n".join(lines) + "\n", mimetype="text/plain; charset=utf-8")
        response.headers["Cache-Control"] = "public, max-age=600"
        return response

    @bp.get("/<newsletter_slug>/posts/<post_slug>")
    def post_detail(newsletter_slug: str, post_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        post = _db().get_post(newsletter_slug, post_slug)
        if post is None or post.published_at is None:
            abort(404)

        fmt = _response_format()
        if fmt == "json":
            response = jsonify(newsletter=newsletter._asdict(), post=post._asdict())
            response.headers["Cache-Control"] = "public, max-age=600"
            return _vary(response)

        if fmt == "markdown":
            body = f"# {post.title}\n\n{post.body_md}\n"
            response = Response(body, mimetype="text/markdown; charset=utf-8")
            response.headers["Cache-Control"] = "public, max-age=600"
            return _vary(response)

        response = make_response(
            render_template(
                "quillet/post_detail.html",
                newsletter=newsletter,
                post=post,
                post_html=_render_post_html(post.body_md),
            )
        )
        response.headers["Cache-Control"] = "public, max-age=600"
        return _vary(response)

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
            config = _db().get_newsletter_config(newsletter.id)
            description_html = _render_post_html(config.public_description) if config.public_description else ""
            page = _db().list_posts(newsletter_slug, published_only=True)
            return (
                render_template(
                    "quillet/post_list.html",
                    newsletter=newsletter,
                    posts=page.items,
                    next_cursor=page.next_cursor,
                    subscribe_error="Email is required.",
                    subscribe_position=config.subscribe_position or "end",
                    description_html=description_html,
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
    def confirm_form(newsletter_slug: str, token: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        if _wants_json():
            response = jsonify(error="POST required to confirm.")
            return response, 405, {"Allow": "POST"}

        subscriber = _db().get_subscriber_by_token(token)
        if subscriber is not None:
            _db().purge_expired_pending(subscriber.newsletter_id)
            subscriber = _db().get_subscriber_by_token(token)

        return render_template(
            "quillet/subscribe_confirm.html",
            newsletter=newsletter,
            token=token,
            state=_confirm_state(subscriber),
        )

    @bp.post("/<newsletter_slug>/confirm/<token>")
    def confirm_subscription(newsletter_slug: str, token: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        subscriber = _db().get_subscriber_by_token(token)
        already_confirmed = subscriber is not None and subscriber.confirmed_at is not None
        if subscriber is not None and not already_confirmed:
            subscriber = _db().confirm_subscriber(token)
            if subscriber is not None:
                _db().log_event(
                    "confirm",
                    json.dumps({"email": subscriber.email, "newsletter_slug": newsletter_slug}),
                    newsletter_id=newsletter.id,
                )

        if _wants_json():
            if subscriber is None or subscriber.confirmed_at is None:
                return jsonify(error="Invalid or expired token."), 404
            return jsonify(ok=True)

        return render_template(
            "quillet/subscribe_confirm.html",
            newsletter=newsletter,
            token=token,
            state="confirmed" if subscriber is not None and subscriber.confirmed_at is not None else "invalid",
        )

    @bp.get("/<newsletter_slug>/unsubscribe/<token>")
    def unsubscribe_form(newsletter_slug: str, token: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        if _wants_json():
            response = jsonify(error="POST required to unsubscribe.")
            return response, 405, {"Allow": "POST"}

        subscriber = _db().get_subscriber_by_token(token)
        if subscriber is None:
            state = "invalid"
        elif subscriber.unsubscribed:
            state = "unsubscribed"
        else:
            state = "confirm"
        return render_template(
            "quillet/unsubscribe_confirm.html",
            newsletter=newsletter,
            token=token,
            state=state,
        )

    @bp.post("/<newsletter_slug>/unsubscribe/<token>")
    def unsubscribe_submit(newsletter_slug: str, token: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        subscriber = _db().get_subscriber_by_token(token)
        if subscriber is None:
            if _wants_json():
                return jsonify(error="Invalid token."), 404
            return render_template(
                "quillet/unsubscribe_confirm.html",
                newsletter=newsletter,
                token=token,
                state="invalid",
            )

        if not subscriber.unsubscribed:
            _db().unsubscribe(token)
            _db().log_event(
                "unsubscribe",
                json.dumps({"newsletter_slug": newsletter_slug}),
                newsletter_id=newsletter.id,
            )

        if _wants_json():
            return jsonify(ok=True)

        return render_template(
            "quillet/unsubscribe_confirm.html",
            newsletter=newsletter,
            token=token,
            state="unsubscribed",
        )

    @bp.get("/<newsletter_slug>/feed.xml")
    def feed(newsletter_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        page = _db().list_posts(newsletter_slug, published_only=True, limit=RSS_LIMIT)
        posts = page.items
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
        payload, status = api_ops.list_posts(
            _db(),
            newsletter_slug,
            status=request.args.get("status") or "all",
            cursor=request.args.get("cursor"),
            limit=request.args.get("limit"),
        )
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
            section_md=data.get("section_md") or "",
            section_position=data.get("section_position") or "append",
        )
        return jsonify(payload), status

    @bp.post("/<newsletter_slug>/api/posts/<post_slug>/send")
    @require_basic_auth
    def api_send_post(newsletter_slug: str, post_slug: str):
        data = request.get_json(silent=True) or {}
        payload, status = api_ops.send_post(
            current_app._get_current_object(),
            newsletter_slug,
            post_slug,
            section_md=data.get("section_md") or "",
            section_position=data.get("section_position") or "append",
        )
        return jsonify(payload), status

    @bp.get("/<newsletter_slug>/api/subscribers")
    @require_basic_auth
    def api_list_subscribers(newsletter_slug: str):
        payload, status = api_ops.list_subscribers(
            _db(),
            newsletter_slug,
            cursor=request.args.get("cursor"),
            limit=request.args.get("limit"),
        )
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

    @bp.post("/<newsletter_slug>/api/subscribers/invite")
    @require_basic_auth
    def api_invite_subscriber(newsletter_slug: str):
        data = request.get_json(silent=True) or {}
        payload, status = api_ops.invite_subscriber(
            current_app._get_current_object(),
            newsletter_slug,
            email=data.get("email") or "",
            body_md=data.get("body_md") or "",
            reset=data.get("reset", False),
        )
        return jsonify(payload), status

    @bp.post("/<newsletter_slug>/api/subscribers/<int:subscriber_id>/reset")
    @require_basic_auth
    def api_reset_subscriber(newsletter_slug: str, subscriber_id: int):
        payload, status = api_ops.reset_subscriber(_db(), newsletter_slug, subscriber_id)
        return jsonify(payload), status

    @bp.delete("/<newsletter_slug>/api/subscribers/<int:subscriber_id>")
    @require_basic_auth
    def api_delete_subscriber(newsletter_slug: str, subscriber_id: int):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        _db().delete_subscriber(subscriber_id)
        return jsonify(ok=True)
