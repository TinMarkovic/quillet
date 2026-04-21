import json
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
from .models import NewsletterConfig, Subscriber
from .routes import _db, _email, _post_list_url, _post_url, _unsubscribe_url_template


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
        _db().log_event(
            "post_published",
            json.dumps({"post_slug": post_slug, "newsletter_slug": newsletter_slug}),
            newsletter_id=newsletter.id,
        )
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
            config = _db().get_newsletter_config(newsletter.id)
            _email().send_post(
                newsletter,
                post,
                subscribers,
                _unsubscribe_url_template(name, newsletter_slug),
                config,
                post_url=_post_url(name, newsletter_slug, post_slug),
                post_list_url=_post_list_url(name, newsletter_slug),
            )
            _db().mark_sent(post.id)
            _db().log_event(
                "post_sent",
                json.dumps(
                    {
                        "post_slug": post_slug,
                        "newsletter_slug": newsletter_slug,
                        "recipients": len(subscribers),
                    }
                ),
                newsletter_id=newsletter.id,
            )

        return redirect(url_for(f"{name}.dashboard", newsletter_slug=newsletter_slug))

    @bp.post("/<newsletter_slug>/admin/posts/<post_slug>/delete")
    @require_basic_auth
    def delete_post(newsletter_slug: str, post_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        post = _db().get_post(newsletter_slug, post_slug)
        if post is None:
            abort(404)

        _db().delete_post(post.id)
        _db().log_event(
            "post_deleted",
            json.dumps({"post_slug": post_slug, "newsletter_slug": newsletter_slug}),
            newsletter_id=newsletter.id,
        )
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

    @bp.post("/<newsletter_slug>/admin/subscribers/<int:subscriber_id>/delete")
    @require_basic_auth
    def delete_subscriber(newsletter_slug: str, subscriber_id: int):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        _db().delete_subscriber(subscriber_id)
        _db().log_event(
            "subscriber_deleted",
            json.dumps({"subscriber_id": subscriber_id, "newsletter_slug": newsletter_slug}),
            newsletter_id=newsletter.id,
        )
        return redirect(url_for(f"{name}.subscriber_list", newsletter_slug=newsletter_slug))

    @bp.get("/<newsletter_slug>/admin/settings")
    @require_basic_auth
    def settings(newsletter_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        config = _db().get_newsletter_config(newsletter.id)
        return render_template(
            "quillet/admin/settings.html",
            newsletter=newsletter,
            config=config,
            saved=request.args.get("saved") == "1",
        )

    @bp.post("/<newsletter_slug>/admin/settings")
    @require_basic_auth
    def save_settings(newsletter_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        name_val = request.form.get("name", "").strip()
        from_name = request.form.get("from_name", "").strip()
        from_email = request.form.get("from_email", "").strip()
        reply_to = request.form.get("reply_to", "").strip() or None

        if not name_val or not from_email:
            config = _db().get_newsletter_config(newsletter.id)
            return (
                render_template(
                    "quillet/admin/settings.html",
                    newsletter=newsletter,
                    config=config,
                    saved=False,
                    error="Newsletter name and From Email are required.",
                ),
                400,
            )

        newsletter = _db().update_newsletter(newsletter.id, name_val, from_name, from_email, reply_to)

        config = NewsletterConfig(
            newsletter_id=newsletter.id,
            subject_prefix=request.form.get("subject_prefix", "").strip() or None,
            email_opener=request.form.get("email_opener", "").strip() or None,
            email_footer=request.form.get("email_footer", "").strip() or None,
            post_header_template=request.form.get("post_header_template", "").strip() or None,
        )
        _db().save_newsletter_config(config)

        return redirect(url_for(f"{name}.settings", newsletter_slug=newsletter.slug, saved="1"))

    @bp.post("/<newsletter_slug>/admin/posts/<post_slug>/send-test")
    @require_basic_auth
    def send_test_post(newsletter_slug: str, post_slug: str):
        newsletter = _db().get_newsletter(newsletter_slug)
        if newsletter is None:
            abort(404)

        post = _db().get_post(newsletter_slug, post_slug)
        if post is None:
            abort(404)

        test_email = request.form.get("test_email", "").strip()
        if not test_email:
            test_email = newsletter.reply_to or newsletter.from_email

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
            _unsubscribe_url_template(name, newsletter_slug),
            test_config,
            post_url=_post_url(name, newsletter_slug, post_slug),
            post_list_url=_post_list_url(name, newsletter_slug),
        )

        return redirect(
            url_for(f"{name}.edit_post", newsletter_slug=newsletter_slug, post_slug=post_slug, test_sent="1")
        )
