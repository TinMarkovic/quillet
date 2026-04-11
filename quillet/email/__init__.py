from typing import Protocol

from ..models import Newsletter, Post, Subscriber


class EmailSender(Protocol):
    def send_confirmation(
        self,
        newsletter: Newsletter,
        subscriber: Subscriber,
        confirm_url: str,
    ) -> None: ...

    def send_post(
        self,
        newsletter: Newsletter,
        post: Post,
        subscribers: list[Subscriber],
        unsubscribe_url_template: str,
    ) -> None: ...
