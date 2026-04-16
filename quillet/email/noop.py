import logging

from ..models import Newsletter, NewsletterConfig, Post, Subscriber

logger = logging.getLogger("quillet.email.noop")


class NoopSender:
    """
    Email sender that logs messages instead of sending them.
    Useful for local development and testing — no SMTP config required.
    """

    def send_confirmation(
        self,
        newsletter: Newsletter,
        subscriber: Subscriber,
        confirm_url: str,
        config: NewsletterConfig | None = None,
    ) -> None:
        logger.info(
            "NOOP confirmation | newsletter=%s to=%s confirm_url=%s",
            newsletter.slug,
            subscriber.email,
            confirm_url,
        )

    def send_post(
        self,
        newsletter: Newsletter,
        post: Post,
        subscribers: list[Subscriber],
        unsubscribe_url_template: str,
        config: NewsletterConfig | None = None,
    ) -> None:
        logger.info(
            "NOOP send_post | newsletter=%s post=%s recipients=%d",
            newsletter.slug,
            post.slug,
            len(subscribers),
        )
