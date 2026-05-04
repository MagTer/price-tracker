"""aiosmtplib email service implementing IEmailService."""

import os
from email.message import EmailMessage as StdEmailMessage

import aiosmtplib

from domain.protocols.email import EmailMessage, EmailResult


class SmtpEmailService:
    """Email service using aiosmtplib (SMTP)."""

    def __init__(self) -> None:
        self._host = os.getenv("SMTP_HOST", "")
        self._port = int(os.getenv("SMTP_PORT", "587"))
        self._user = os.getenv("SMTP_USER", "")
        self._password = os.getenv("SMTP_PASSWORD", "")
        self._from_addr = os.getenv("SMTP_FROM", "")

    def is_configured(self) -> bool:
        return bool(self._host and self._user and self._password and self._from_addr)

    async def send(self, message: EmailMessage) -> EmailResult:
        if not self.is_configured():
            return EmailResult(
                success=False,
                error="SMTP not configured (missing host, user, password, or from address)",
            )

        msg = StdEmailMessage()
        msg["From"] = self._from_addr
        msg["To"] = ", ".join(message.to)
        msg["Subject"] = message.subject
        if message.reply_to:
            msg["Reply-To"] = message.reply_to

        msg.add_alternative(message.html_body, subtype="html")
        if message.text_body:
            msg.set_content(message.text_body)

        try:
            await aiosmtplib.send(
                msg,
                hostname=self._host,
                port=self._port,
                username=self._user,
                password=self._password,
                start_tls=True,
            )
            return EmailResult(success=True)
        except Exception as e:
            return EmailResult(success=False, error=str(e))

    async def send_batch(self, messages: list[EmailMessage]) -> list[EmailResult]:
        results: list[EmailResult] = []
        for message in messages:
            results.append(await self.send(message))
        return results
