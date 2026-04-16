import json

import requests

from ..models import Newsletter, NewsletterConfig, Post, Subscriber
from ._utils import md_to_html, md_to_plain
from .smtp import _DEFAULT_OPENER_HTML, _DEFAULT_OPENER_TEXT, _render_opener

_MAILGUN_API_BASES = {
    "us": "https://api.mailgun.net/v3",
    "eu": "https://api.eu.mailgun.net/v3",
}

_DEFAULT_FOOTER_TEXT = "---\nUnsubscribe: %recipient.unsubscribe_url%"
_DEFAULT_FOOTER_HTML = '<hr><p><small><a href="%recipient.unsubscribe_url%">Unsubscribe</a></small></p>'


def _render_footer_batch(config: NewsletterConfig | None) -> tuple[str, str]:
    """Return footer strings using Mailgun's %recipient.unsubscribe_url% merge tag."""
    if config and config.email_footer:
        rendered_text = config.email_footer.format(unsubscribe_url="%recipient.unsubscribe_url%")
        rendered_html = config.email_footer.format(unsubscribe_url="%recipient.unsubscribe_url%")
        return md_to_plain(rendered_text), md_to_html(rendered_html)

    return _DEFAULT_FOOTER_TEXT, _DEFAULT_FOOTER_HTML


class MailgunSender:
    def __init__(
        self,
        api_key: str,
        domain: str,
        region: str = "us",
        sender_email: str | None = None,
        sender_name: str | None = None,
        subject_prefix: str = "",
    ) -> None:
        if region not in _MAILGUN_API_BASES:
            raise ValueError(f"Unknown Mailgun region {region!r}. Use 'us' or 'eu'.")
        self._api_key = api_key
        self._domain = domain
        self._api_base = _MAILGUN_API_BASES[region]
        self._sender_email = sender_email or f"quillet@{domain}"
        self._sender_name = sender_name
        self._subject_prefix = subject_prefix

    def _post(self, endpoint: str, data: dict) -> None:
        resp = requests.post(
            f"{self._api_base}/{self._domain}/{endpoint}",
            auth=("api", self._api_key),
            data=data,
            timeout=30,
        )
        resp.raise_for_status()

    def _from_field(self, newsletter: Newsletter) -> str:
        name = self._sender_name or newsletter.from_name
        email = self._sender_email
        return f"{name} <{email}>"

    def _subject(self, title: str, config: NewsletterConfig | None) -> str:
        prefix = (config and config.subject_prefix) or self._subject_prefix
        return f"{prefix}{title}" if prefix else title

    def send_confirmation(
        self,
        newsletter: Newsletter,
        subscriber: Subscriber,
        confirm_url: str,
        config: NewsletterConfig | None = None,
    ) -> None:
        opener_text, opener_html = _render_opener(config, newsletter.name, confirm_url)

        self._post(
            "messages",
            {
                "from": self._from_field(newsletter),
                "h:Reply-To": newsletter.from_email,
                "to": subscriber.email,
                "subject": self._subject(f"Confirm your subscription to {newsletter.name}", config),
                "text": opener_text,
                "html": opener_html,
            },
        )

    def send_post(
        self,
        newsletter: Newsletter,
        post: Post,
        subscribers: list[Subscriber],
        unsubscribe_url_template: str,
        config: NewsletterConfig | None = None,
    ) -> None:
        """
        Send a post to all subscribers using Mailgun batch sending.
        unsubscribe_url_template should contain {token} as a placeholder.
        """
        if not subscribers:
            return

        reply_to = newsletter.reply_to or newsletter.from_email
        footer_text, footer_html = _render_footer_batch(config)

        recipient_variables = {
            sub.email: {
                "unsubscribe_url": unsubscribe_url_template.format(token=sub.token),
            }
            for sub in subscribers
        }

        self._post(
            "messages",
            {
                "from": self._from_field(newsletter),
                "h:Reply-To": reply_to,
                "to": [sub.email for sub in subscribers],
                "subject": self._subject(post.title, config),
                "text": f"{md_to_plain(post.body_md)}\n\n{footer_text}",
                "html": f"{md_to_html(post.body_md)}{footer_html}",
                "recipient-variables": json.dumps(recipient_variables),
            },
        )
