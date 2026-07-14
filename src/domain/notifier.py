"""Price tracker notifications using platform email service."""

from __future__ import annotations

import html
import logging
from decimal import Decimal

from domain.protocols.email import EmailMessage, IEmailService

logger = logging.getLogger(__name__)


class PriceNotifier:
    """Send price alerts using the platform email service.

    This class handles price-specific email formatting and delegates
    actual sending to the injected IEmailService.
    """

    def __init__(self, email_service: IEmailService) -> None:
        """Initialize the price notifier.

        Args:
            email_service: The email service to use for sending.
        """
        self._email_service = email_service

    async def send_price_alert(
        self,
        to_email: str,
        product_name: str,
        store_name: str,
        current_price: Decimal,
        target_price: Decimal | None,
        offer_type: str | None,
        offer_details: str | None,
        product_url: str | None = None,
        price_drop_percent: float | None = None,
        unit_price_sek: Decimal | None = None,
        unit_price_drop_percent: float | None = None,
    ) -> bool:
        """Send price drop alert email.

        Returns:
            True if email was sent successfully.
        """
        subject = f"Prisvarning: {product_name} hos {store_name}"
        html_body = self._build_alert_html(
            product_name=product_name,
            store_name=store_name,
            current_price=current_price,
            target_price=target_price,
            offer_type=offer_type,
            offer_details=offer_details,
            product_url=product_url,
            price_drop_percent=price_drop_percent,
            unit_price_sek=unit_price_sek,
            unit_price_drop_percent=unit_price_drop_percent,
        )

        message = EmailMessage(
            to=[to_email],
            subject=subject,
            html_body=html_body,
        )

        result = await self._email_service.send(message)
        return result.success

    async def send_weekly_summary(
        self,
        to_email: str,
        deals: list[dict[str, str | Decimal | None]],
        watched_products: list[dict[str, str | Decimal | None]],
    ) -> bool:
        """Send weekly price summary email.

        Returns:
            True if email was sent successfully.
        """
        subject = "Veckans prisoversikt - Prisspaning"
        html_body = self._build_summary_html(deals, watched_products)

        message = EmailMessage(
            to=[to_email],
            subject=subject,
            html_body=html_body,
        )

        result = await self._email_service.send(message)
        return result.success

    def _is_safe_url(self, url: str | None) -> bool:
        """Validate that URL uses a safe scheme (http/https).

        Args:
            url: The URL to validate.

        Returns:
            True if URL is safe, False otherwise.
        """
        if not url:
            return False
        return url.startswith("http://") or url.startswith("https://")

    def _build_alert_html(
        self,
        product_name: str,
        store_name: str,
        current_price: Decimal,
        target_price: Decimal | None,
        offer_type: str | None,
        offer_details: str | None,
        product_url: str | None,
        price_drop_percent: float | None = None,
        unit_price_sek: Decimal | None = None,
        unit_price_drop_percent: float | None = None,
    ) -> str:
        """Build HTML for price alert email."""
        # Escape all user-controlled text to prevent HTML injection
        safe_product_name = html.escape(product_name)
        safe_store_name = html.escape(store_name)

        target_row = ""
        if target_price:
            target_row = f"""
            <tr>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">Ditt malpris:</td>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">{target_price} kr</td>
            </tr>"""

        price_drop_row = ""
        if price_drop_percent is not None:
            price_drop_row = f"""
            <tr>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">Prisfall:</td>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">
                    <strong style="color: #22c55e;">
                        {price_drop_percent:.1f}% under ordinarie pris
                    </strong>
                </td>
            </tr>"""

        unit_price_row = ""
        if unit_price_sek is not None:
            unit_price_row = f"""
            <tr>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">Jamforelsepris:</td>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">
                    <strong>{unit_price_sek} kr/enhet</strong>
                </td>
            </tr>"""

        unit_price_drop_row = ""
        if unit_price_drop_percent is not None:
            unit_price_drop_row = f"""
            <tr>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">Jamforelsepris-fall:</td>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">
                    <strong style="color: #22c55e;">
                        {unit_price_drop_percent:.1f}% under ordinarie jamforelsepris
                    </strong>
                </td>
            </tr>"""

        offer_row = ""
        if offer_type:
            safe_offer_type = html.escape(offer_type)
            safe_details = f" - {html.escape(offer_details)}" if offer_details else ""
            offer_row = f"""
            <tr>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">Erbjudande:</td>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">
                    <span style="background: #22c55e; color: white; padding: 2px 8px;
                                border-radius: 4px;">
                        {safe_offer_type}
                    </span>{safe_details}
                </td>
            </tr>"""

        link_section = ""
        if product_url and self._is_safe_url(product_url):
            # URL is validated, but still escape it for HTML attribute safety
            safe_url = html.escape(product_url, quote=True)
            link_section = f"""
            <p style="margin-top: 20px;">
                <a href="{safe_url}"
                   style="background: #2563eb; color: white; padding: 10px 20px;
                          text-decoration: none; border-radius: 4px;">Se produkten</a>
            </p>"""

        return f"""
        <!DOCTYPE html>
        <html lang="sv">
        <head><meta charset="UTF-8"></head>
        <body style="font-family: Arial, sans-serif; max-width: 600px;
                     margin: 0 auto; padding: 20px;">
            <h2 style="color: #1e3a5f;">Prisvarning!</h2>
            <p><strong>{safe_product_name}</strong> hos <strong>{safe_store_name}</strong>
               har ett bra pris.</p>

            <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
                <tr>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">Aktuellt pris:</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee; font-size: 1.2em;">
                        <strong style="color: #22c55e;">{current_price} kr</strong>
                    </td>
                </tr>
                {target_row}
                {price_drop_row}
                {unit_price_row}
                {unit_price_drop_row}
                {offer_row}
            </table>
            {link_section}

            <hr style="margin-top: 30px; border: none; border-top: 1px solid #eee;">
            <p style="color: #666; font-size: 0.9em;">
                Detta mail skickades av Prisspaning. Du far detta for att du bevakar produkten.
            </p>
        </body>
        </html>
        """

    def _build_summary_html(
        self,
        deals: list[dict[str, str | Decimal | None]],
        watched_products: list[dict[str, str | Decimal | None]],
    ) -> str:
        """Build HTML for weekly summary email."""
        # Build deals section
        deals_html = ""
        if deals:
            deals_rows = ""
            for deal in deals[:10]:  # Limit to top 10
                product_name = deal.get("product_name", "")
                store_name = deal.get("store_name", "")
                offer_price = deal.get("offer_price_sek", "")
                offer_type = deal.get("offer_type", "")

                # Escape all user-controlled data
                safe_product_name = html.escape(str(product_name))
                safe_store_name = html.escape(str(store_name))
                safe_offer_type = html.escape(str(offer_type))

                deals_rows += f"""
                <tr>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">
                        {safe_product_name}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">
                        {safe_store_name}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;
                               color: #22c55e; font-weight: bold;">
                        {offer_price} kr
                    </td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">
                        <span style="background: #f59e0b; color: white;
                                     padding: 2px 6px; border-radius: 3px;
                                     font-size: 0.8em;">
                            {safe_offer_type}
                        </span>
                    </td>
                </tr>"""

            deals_html = f"""
            <h3 style="color: #1e3a5f; margin-top: 30px;">Aktuella erbjudanden</h3>
            <table style="width: 100%; border-collapse: collapse;">
                <thead>
                    <tr style="background: #f3f4f6;">
                        <th style="padding: 8px; text-align: left;">Produkt</th>
                        <th style="padding: 8px; text-align: left;">Butik</th>
                        <th style="padding: 8px; text-align: left;">Pris</th>
                        <th style="padding: 8px; text-align: left;">Typ</th>
                    </tr>
                </thead>
                <tbody>{deals_rows}</tbody>
            </table>"""

        # Build watched products section
        watched_html = ""
        if watched_products:
            watched_rows = ""
            for product in watched_products:
                name = product.get("name", "")
                lowest_price = product.get("lowest_price", "N/A")
                store_name = product.get("store_name", "")
                # "kr/st"-style when the row carries a computed kr/enhet, plain "kr"
                # for the absolute-price fallback (and for rows without a label).
                price_label = product.get("price_label") or "kr"

                # Escape all user-controlled data
                safe_name = html.escape(str(name))
                safe_store_name = html.escape(str(store_name))
                safe_price_label = html.escape(str(price_label))

                watched_rows += f"""
                <tr>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">
                        {safe_name}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">
                        {lowest_price} {safe_price_label}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">
                        {safe_store_name}</td>
                </tr>"""

            watched_html = f"""
            <h3 style="color: #1e3a5f; margin-top: 30px;">Dina bevakade produkter</h3>
            <table style="width: 100%; border-collapse: collapse;">
                <thead>
                    <tr style="background: #f3f4f6;">
                        <th style="padding: 8px; text-align: left;">Produkt</th>
                        <th style="padding: 8px; text-align: left;">Lagsta pris</th>
                        <th style="padding: 8px; text-align: left;">Butik</th>
                    </tr>
                </thead>
                <tbody>{watched_rows}</tbody>
            </table>"""

        return f"""
        <!DOCTYPE html>
        <html lang="sv">
        <head><meta charset="UTF-8"></head>
        <body style="font-family: Arial, sans-serif; max-width: 600px;
                     margin: 0 auto; padding: 20px;">
            <h2 style="color: #1e3a5f;">Veckans prisoversikt</h2>
            <p>Har ar en sammanfattning av priser och erbjudanden denna vecka.</p>
            {deals_html}
            {watched_html}

            <hr style="margin-top: 30px; border: none; border-top: 1px solid #eee;">
            <p style="color: #666; font-size: 0.9em;">
                Detta mail skickades av Prisspaning.
            </p>
        </body>
        </html>
        """
