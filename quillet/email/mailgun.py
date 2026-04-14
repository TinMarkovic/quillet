import json

import requests

from ..models import Newsletter, Post, Subscriber
from ._utils import md_to_html, md_to_plain

_MAILGUN_API_BASES = {
    "us": "https://api.mailgun.net/v3",
    "eu": "https://api.eu.mailgun.net/v3",
}


class MailgunSender:
    def __init__(
        self,
        api_key: str,
        domain: str,
        region: str = "us",
        sender_email: str | None = None,
        subject_prefix: str = "",
    ) -> None:
        if region not in _MAILGUN_API_BASES:
            raise ValueError(f"Unknown Mailgun region {region!r}. Use 'us' or 'eu'.")
        self._api_key = api_key
        self._domain = domain
        self._api_base = _MAILGUN_API_BASES[region]
        self._sender_email = sender_email or f"quillet@{domain}"
        self._subject_prefix = subject_prefix

    def _post(self, endpoint: str, data: dict) -> None:
        resp = requests.post(
            f"{self._api_base}/{self._domain}/{endpoint}",
            auth=("api", self._api_key),
            data=data,
            timeout=30,
        )
        resp.raise_for_status()

    def _from_field(self, display_name: str) -> str:
        return f"{display_name} <{self._sender_email}>"

    def _subject(self, title: str) -> str:
        return f"{self._subject_prefix}{title}" if self._subject_prefix else title

    def send_confirmation(
        self,
        newsletter: Newsletter,
        subscriber: Subscriber,
        confirm_url: str,
    ) -> None:
        self._post(
            "messages",
            {
                "from": self._from_field(newsletter.from_name),
                "h:Reply-To": newsletter.from_email,
                "to": subscriber.email,
                "subject": self._subject(f"Confirm your subscription to {newsletter.name}"),
                "text": (
                    f"Hi,\n\nPlease confirm your subscription to {newsletter.name} "
                    f"by clicking the link below:\n\n{confirm_url}\n\n"
                    "If you did not subscribe, you can safely ignore this email."
                ),
                "html": (
                    f"<p>Hi,</p>"
                    f"<p>Please confirm your subscription to <strong>{newsletter.name}</strong> "
                    f"by clicking the link below:</p>"
                    f'<p><a href="{confirm_url}">Confirm subscription</a></p>'
                    f"<p>If you did not subscribe, you can safely ignore this email.</p>"
                ),
            },
        )

    def send_post(
        self,
        newsletter: Newsletter,
        post: Post,
        subscribers: list[Subscriber],
        unsubscribe_url_template: str,
    ) -> None:
        """
        Send a post to all subscribers using Mailgun batch sending.
        unsubscribe_url_template should contain {token} as a placeholder.
        """
        if not subscribers:
            return

        reply_to = newsletter.reply_to or newsletter.from_email

        recipient_variables = {
            sub.email: {
                "unsubscribe_url": unsubscribe_url_template.format(token=sub.token),
            }
            for sub in subscribers
        }

        self._post(
            "messages",
            {
                "from": self._from_field(newsletter.from_name),
                "h:Reply-To": reply_to,
                "to": [sub.email for sub in subscribers],
                "subject": self._subject(post.title),
                "text": (f"{md_to_plain(post.body_md)}\n\n" "---\n" "Unsubscribe: %recipient.unsubscribe_url%"),
                "html": (
                    f"{md_to_html(post.body_md)}"
                    "<hr>"
                    '<p><small><a href="%recipient.unsubscribe_url%">Unsubscribe</a></small></p>'
                ),
                "recipient-variables": json.dumps(recipient_variables),
            },
        )
