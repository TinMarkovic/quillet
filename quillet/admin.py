import re

from flask import (
    Blueprint,
    abort,
    redirect,
    render_template,
    request,
    url_for,
)

from .auth import require_basic_auth
from .routes import _db, _email, _unsubscribe_url_template


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[\s_-]+", "-", text)


def register_admin_routes(bp: Blueprint) -> None:
    name = bp.name

    @bp.get("/<newsletter_slug>/admin/")
    @require_basic_auth
    def dashboard(newsletter_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        posts = _db().list_posts(newsletter_slug, published_only=False)
        subscribers = _db().list_all_subscribers(newsletter_slug)
        confirmed_count = sum(1 for s in subscribers if s.confirmed_at is not None)

        return render_template(
            "quillet/admin/dashboard.html",
            newsletter=newsletter,
            posts=posts,
            subscriber_count=len(subscribers),
            confirmed_count=confirmed_count,
        )

    @bp.get("/<newsletter_slug>/admin/posts/new")
    @require_basic_auth
    def new_post(newsletter_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)
        return render_template(
            "quillet/admin/post_form.html",
            newsletter=newsletter,
            post=None,
            error=None,
        )

    @bp.post("/<newsletter_slug>/admin/posts/new")
    @require_basic_auth
    def create_post(newsletter_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        title = request.form.get("title", "").strip()
        slug = request.form.get("slug", "").strip() or _slugify(title)
        body_md = request.form.get("body_md", "").strip()

        if not title or not body_md:
            return (
                render_template(
                    "quillet/admin/post_form.html",
                    newsletter=newsletter,
                    post=None,
                    error="Title and body are required.",
                ),
                400,
            )

        post = _db().create_post(newsletter_slug, title, slug, body_md)
        return redirect(url_for(f"{name}.edit_post", newsletter_slug=newsletter_slug, post_slug=post.slug))

    @bp.get("/<newsletter_slug>/admin/posts/<post_slug>/edit")
    @require_basic_auth
    def edit_post(newsletter_slug: str, post_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        post = _db().get_post(newsletter_slug, post_slug)
        if post is None:
            abort(404)

        return render_template(
            "quillet/admin/post_form.html",
            newsletter=newsletter,
            post=post,
            error=None,
        )

    @bp.post("/<newsletter_slug>/admin/posts/<post_slug>/edit")
    @require_basic_auth
    def save_post(newsletter_slug: str, post_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        post = _db().get_post(newsletter_slug, post_slug)
        if post is None:
            abort(404)

        title = request.form.get("title", "").strip()
        new_slug = request.form.get("slug", "").strip() or _slugify(title)
        body_md = request.form.get("body_md", "").strip()

        if not title or not body_md:
            return (
                render_template(
                    "quillet/admin/post_form.html",
                    newsletter=newsletter,
                    post=post,
                    error="Title and body are required.",
                ),
                400,
            )

        updated = _db().update_post(post.id, title, new_slug, body_md)
        return redirect(url_for(f"{name}.edit_post", newsletter_slug=newsletter_slug, post_slug=updated.slug))

    @bp.post("/<newsletter_slug>/admin/posts/<post_slug>/publish")
    @require_basic_auth
    def publish_post(newsletter_slug: str, post_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        post = _db().get_post(newsletter_slug, post_slug)
        if post is None:
            abort(404)

        _db().publish_post(post.id)
        return redirect(url_for(f"{name}.dashboard", newsletter_slug=newsletter_slug))

    @bp.post("/<newsletter_slug>/admin/posts/<post_slug>/send")
    @require_basic_auth
    def send_post(newsletter_slug: str, post_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        post = _db().get_post(newsletter_slug, post_slug)
        if post is None:
            abort(404)

        if post.published_at is None:
            return redirect(url_for(f"{name}.edit_post", newsletter_slug=newsletter_slug, post_slug=post_slug))

        if post.sent_at is None:
            subscribers = _db().list_confirmed_subscribers(newsletter_slug)
            _email().send_post(newsletter, post, subscribers, _unsubscribe_url_template(name, newsletter_slug))
            _db().mark_sent(post.id)

        return redirect(url_for(f"{name}.dashboard", newsletter_slug=newsletter_slug))

    @bp.get("/<newsletter_slug>/admin/subscribers")
    @require_basic_auth
    def subscriber_list(newsletter_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        subscribers = _db().list_all_subscribers(newsletter_slug)
        return render_template(
            "quillet/admin/subscribers.html",
            newsletter=newsletter,
            subscribers=subscribers,
        )
