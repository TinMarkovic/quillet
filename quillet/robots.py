"""Generate a site-root robots.txt from the live newsletter list."""

from flask import Flask, Response

from .db import NewsletterRepository


def robots_txt(
    db: NewsletterRepository,
    *,
    url_prefix: str = "",
    base_url: str = "",
) -> str:
    """
    Return a robots.txt document for the current newsletters.

    Sitemap URLs are absolute when ``base_url`` is set (recommended).
    ``url_prefix`` is the blueprint prefix, e.g. ``/newsletter``.
    """
    prefix = "/" + url_prefix.strip("/") if url_prefix.strip("/") else ""
    origin = base_url.rstrip("/")
    slugs = db.list_newsletter_slugs()

    lines = [
        "User-agent: *",
        "Allow: /",
        f"Disallow: {prefix}/*/admin",
        f"Disallow: {prefix}/*/api",
    ]
    for slug in slugs:
        sitemap_path = f"{prefix}/{slug}/sitemap.xml"
        sitemap_url = f"{origin}{sitemap_path}" if origin else sitemap_path
        lines.append(f"Sitemap: {sitemap_url}")
    return "\n".join(lines) + "\n"


def register_robots(
    app: Flask,
    db: NewsletterRepository,
    *,
    url_prefix: str = "",
    base_url: str = "",
) -> None:
    """Mount ``GET /robots.txt`` on ``app`` using :func:`robots_txt`."""

    if "quillet_robots" in app.view_functions:
        return

    def _robots_view():
        body = robots_txt(
            db,
            url_prefix=url_prefix,
            base_url=base_url or app.config.get("QUILLET_BASE_URL", ""),
        )
        return Response(body, mimetype="text/plain; charset=utf-8")

    app.add_url_rule("/robots.txt", endpoint="quillet_robots", view_func=_robots_view)
