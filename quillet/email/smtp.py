import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from ..models import Newsletter, NewsletterConfig, Post, Subscriber
from ._utils import md_to_html, md_to_plain

_DEFAULT_OPENER_TEXT = (
    "Hi,\n\n"
    "Please confirm your subscription to {newsletter_name} "
    "by clicking the link below:\n\n{confirm_url}\n\n"
    "If you did not subscribe, you can safely ignore this email."
)
_DEFAULT_OPENER_HTML = (
    "<p>Hi,</p>"
    "<p>Please confirm your subscription to <strong>{newsletter_name}</strong> "
    "by clicking the link below:</p>"
    '<p><a href="{confirm_url}">Confirm subscription</a></p>'
    "<p>If you did not subscribe, you can safely ignore this email.</p>"
)

_DEFAULT_FOOTER_TEXT = "---\nUnsubscribe: {unsubscribe_url}"
_DEFAULT_FOOTER_HTML = '<hr><p><small><a href="{unsubscribe_url}">Unsubscribe</a></small></p>'


def _render_opener(
    config: NewsletterConfig | None,
    newsletter_name: str,
    confirm_url: str,
) -> tuple[str, str]:
    if config and config.email_opener:
        rendered = config.email_opener.format(
            newsletter_name=newsletter_name,
            confirm_url=confirm_url,
        )
        return md_to_plain(rendered), md_to_html(rendered)

    return (
        _DEFAULT_OPENER_TEXT.format(newsletter_name=newsletter_name, confirm_url=confirm_url),
        _DEFAULT_OPENER_HTML.format(newsletter_name=newsletter_name, confirm_url=confirm_url),
    )


def _render_footer(config: NewsletterConfig | None, unsubscribe_url: str) -> tuple[str, str]:
    if config and config.email_footer:
        rendered = config.email_footer.format(unsubscribe_url=unsubscribe_url)
        return md_to_plain(rendered), md_to_html(rendered)

    return (
        _DEFAULT_FOOTER_TEXT.format(unsubscribe_url=unsubscribe_url),
        _DEFAULT_FOOTER_HTML.format(unsubscribe_url=unsubscribe_url),
    )


class SmtpSender:
    def __init__(
        self,
        from_email: str,
        from_name: str = "",
        host: str = "localhost",
        port: int = 587,
        username: str | None = None,
        password: str | None = None,
        use_tls: bool = True,
        subject_prefix: str = "",
    ) -> None:
        self._from_email = from_email
        self._from_name = from_name
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._subject_prefix = subject_prefix

    def _from_field(self, newsletter: Newsletter) -> str:
        name = newsletter.from_name or self._from_name
        email = newsletter.from_email or self._from_email
        if name:
            return f"{name} <{email}>"
        return email

    def _subject(self, title: str, config: NewsletterConfig | None) -> str:
        prefix = (config and config.subject_prefix) or self._subject_prefix
        return f"{prefix}{title}" if prefix else title

    def _connect(self) -> smtplib.SMTP:
        smtp = smtplib.SMTP(self._host, self._port)
        if self._use_tls:
            smtp.starttls()
        if self._username and self._password:
            smtp.login(self._username, self._password)
        return smtp

    def _send(self, smtp: smtplib.SMTP, msg: MIMEMultipart) -> None:
        smtp.sendmail(self._from_email, msg["To"], msg.as_string())

    def send_confirmation(
        self,
        newsletter: Newsletter,
        subscriber: Subscriber,
        confirm_url: str,
        config: NewsletterConfig | None = None,
    ) -> None:
        opener_text, opener_html = _render_opener(config, newsletter.name, confirm_url)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = self._subject(f"Confirm your subscription to {newsletter.name}", config)
        msg["From"] = self._from_field(newsletter)
        msg["To"] = subscriber.email
        msg["Reply-To"] = newsletter.from_email

        msg.attach(MIMEText(opener_text, "plain"))
        msg.attach(MIMEText(opener_html, "html"))

        with self._connect() as smtp:
            self._send(smtp, msg)

    def send_post(
        self,
        newsletter: Newsletter,
        post: Post,
        subscribers: list[Subscriber],
        unsubscribe_url_template: str,
        config: NewsletterConfig | None = None,
    ) -> None:
        """
        Sends one email per subscriber (no batch merge — SMTP has no built-in
        per-recipient variable substitution). For large lists use MailgunSender.
        """
        if not subscribers:
            return

        reply_to = newsletter.reply_to or newsletter.from_email
        body_text = md_to_plain(post.body_md)
        body_html = md_to_html(post.body_md)

        with self._connect() as smtp:
            for subscriber in subscribers:
                unsubscribe_url = unsubscribe_url_template.format(token=subscriber.token)
                footer_text, footer_html = _render_footer(config, unsubscribe_url)

                msg = MIMEMultipart("alternative")
                msg["Subject"] = self._subject(post.title, config)
                msg["From"] = self._from_field(newsletter)
                msg["To"] = subscriber.email
                msg["Reply-To"] = reply_to

                msg.attach(MIMEText(f"{body_text}\n\n{footer_text}", "plain"))
                msg.attach(MIMEText(f"{body_html}{footer_html}", "html"))
                self._send(smtp, msg)
