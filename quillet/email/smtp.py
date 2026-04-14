import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from ..models import Newsletter, Post, Subscriber
from ._utils import md_to_html, md_to_plain


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

    def _from_field(self) -> str:
        if self._from_name:
            return f"{self._from_name} <{self._from_email}>"
        return self._from_email

    def _subject(self, title: str) -> str:
        return f"{self._subject_prefix}{title}" if self._subject_prefix else title

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
    ) -> None:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = self._subject(f"Confirm your subscription to {newsletter.name}")
        msg["From"] = self._from_field()
        msg["To"] = subscriber.email
        msg["Reply-To"] = newsletter.from_email

        text = (
            f"Hi,\n\nPlease confirm your subscription to {newsletter.name} "
            f"by clicking the link below:\n\n{confirm_url}\n\n"
            "If you did not subscribe, you can safely ignore this email."
        )
        html = (
            f"<p>Hi,</p>"
            f"<p>Please confirm your subscription to <strong>{newsletter.name}</strong> "
            f"by clicking the link below:</p>"
            f'<p><a href="{confirm_url}">Confirm subscription</a></p>'
            f"<p>If you did not subscribe, you can safely ignore this email.</p>"
        )

        msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))

        with self._connect() as smtp:
            self._send(smtp, msg)

    def send_post(
        self,
        newsletter: Newsletter,
        post: Post,
        subscribers: list[Subscriber],
        unsubscribe_url_template: str,
    ) -> None:
        """
        Sends one email per subscriber (no batch merge — SMTP has no built-in
        per-recipient variable substitution). For large lists use MailgunSender.
        """
        if not subscribers:
            return

        reply_to = newsletter.reply_to or newsletter.from_email

        with self._connect() as smtp:
            for subscriber in subscribers:
                unsubscribe_url = unsubscribe_url_template.format(token=subscriber.token)

                msg = MIMEMultipart("alternative")
                msg["Subject"] = self._subject(post.title)
                msg["From"] = self._from_field()
                msg["To"] = subscriber.email
                msg["Reply-To"] = reply_to

                text = f"{md_to_plain(post.body_md)}\n\n---\nUnsubscribe: {unsubscribe_url}"
                html = (
                    f"{md_to_html(post.body_md)}"
                    "<hr>"
                    f'<p><small><a href="{unsubscribe_url}">Unsubscribe</a></small></p>'
                )

                msg.attach(MIMEText(text, "plain"))
                msg.attach(MIMEText(html, "html"))
                self._send(smtp, msg)
