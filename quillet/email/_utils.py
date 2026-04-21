import html
import math
import re

import markdown2

from ..models import NewsletterConfig, Post

_DEFAULT_POST_HEADER = "# {post_title}"
_WORDS_PER_MINUTE = 200


def reading_time_minutes(body_md: str) -> int:
    word_count = len(body_md.split())
    return max(1, math.ceil(word_count / _WORDS_PER_MINUTE))


def build_post_body_md(
    post: Post,
    newsletter_name: str,
    config: NewsletterConfig | None,
    post_url: str = "",
    post_list_url: str = "",
) -> str:
    """Prepend the configured (or default) header block to the post markdown."""
    template = (config and config.post_header_template) or _DEFAULT_POST_HEADER
    header = template.format(
        post_title=post.title,
        newsletter_name=newsletter_name,
        post_url=post_url,
        post_list_url=post_list_url,
        reading_time=f"{reading_time_minutes(post.body_md)} min read",
    )
    return f"{header}\n\n{post.body_md}"


def md_to_plain(md: str) -> str:
    """Convert markdown to a clean plain-text string for email text/plain parts."""
    raw_html = markdown2.markdown(md)
    text = re.sub(r"<[^>]+>", "", raw_html)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def md_to_html(md: str) -> str:
    """Render markdown to HTML for email HTML parts (same extras as the web view)."""
    return markdown2.markdown(md, extras=["header-ids", "strike", "metadata", "tables"])
