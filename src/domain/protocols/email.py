"""Protocol for email services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class EmailMessage:
    """Represents an email message to be sent."""

    to: list[str]
    subject: str
    html_body: str
    text_body: str | None = None
    reply_to: str | None = None


@dataclass
class EmailResult:
    """Result of an email send operation."""

    success: bool
    message_id: str | None = None
    error: str | None = None


@runtime_checkable
class IEmailService(Protocol):
    """Abstract interface for email services.

    This protocol defines the contract for sending emails.
    Implementations can use different providers (Resend, SendGrid, SMTP, etc.).
    """

    async def send(self, message: EmailMessage) -> EmailResult:
        """Send a single email.

        Args:
            message: The email message to send.

        Returns:
            EmailResult with success status and optional message_id or error.
        """
        ...

    async def send_batch(self, messages: list[EmailMessage]) -> list[EmailResult]:
        """Send multiple emails.

        Args:
            messages: List of email messages to send.

        Returns:
            List of EmailResult, one per message.
        """
        ...

    def is_configured(self) -> bool:
        """Check if the email service is properly configured.

        Returns:
            True if API key and from address are set.
        """
        ...


__all__ = ["EmailMessage", "EmailResult", "IEmailService"]
