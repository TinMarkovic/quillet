from typing import Protocol

from ..models import Newsletter, NewsletterConfig, Post, Subscriber


class EmailSender(Protocol):
    def send_confirmation(
        self,
        newsletter: Newsletter,
        subscriber: Subscriber,
        confirm_url: str,
        config: NewsletterConfig | None = None,
        body_md: str | None = None,
    ) -> None: ...

    def send_post(
        self,
        newsletter: Newsletter,
        post: Post,
        subscribers: list[Subscriber],
        unsubscribe_url_template: str,
        config: NewsletterConfig | None = None,
        post_url: str = "",
        post_list_url: str = "",
        section_md: str = "",
        section_position: str = "append",
    ) -> None: ...
