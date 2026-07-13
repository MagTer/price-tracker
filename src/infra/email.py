"""Resend HTTP API email service implementing IEmailService."""

import logging
import os
from typing import Any

import httpx

from domain.protocols.email import EmailMessage, EmailResult

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"


class ResendEmailService:
    """Email service using Resend's HTTP API (https://resend.com).

    Same provider the source platform used. EMAIL_FROM must be an address
    on a domain verified in the Resend account.
    """

    TIMEOUT = 15.0

    def __init__(self) -> None:
        self._api_key = os.getenv("RESEND_API_KEY", "")
        self._from_addr = os.getenv("EMAIL_FROM", "")

    def is_configured(self) -> bool:
        return bool(self._api_key and self._from_addr)

    async def send(self, message: EmailMessage) -> EmailResult:
        if not self.is_configured():
            return EmailResult(
                success=False,
                error="Resend not configured (missing RESEND_API_KEY or EMAIL_FROM)",
            )

        payload: dict[str, Any] = {
            "from": self._from_addr,
            "to": message.to,
            "subject": message.subject,
            "html": message.html_body,
        }
        if message.text_body:
            payload["text"] = message.text_body
        if message.reply_to:
            payload["reply_to"] = message.reply_to

        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                response = await client.post(
                    RESEND_API_URL,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )

            if response.status_code in (200, 201):
                message_id = response.json().get("id")
                return EmailResult(success=True, message_id=message_id)

            # Resend returns a JSON error body; keep it short in the result
            error_text = response.text[:200]
            logger.warning(
                "Resend API error %d: %s", response.status_code, error_text
            )
            return EmailResult(
                success=False,
                error=f"Resend API {response.status_code}: {error_text}",
            )
        except Exception as e:
            return EmailResult(success=False, error=str(e))

    async def send_batch(self, messages: list[EmailMessage]) -> list[EmailResult]:
        results: list[EmailResult] = []
        for message in messages:
            results.append(await self.send(message))
        return results
