"""Price tracker notifications using platform email service."""

from __future__ import annotations

import html
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from domain.deals import DEAL_BEST, DEAL_STALE_HOURS, DEAL_UNKNOWN, DealRow
from domain.protocols.email import EmailMessage, IEmailService
from domain.schedule import STORE_TIMEZONE

logger = logging.getLogger(__name__)

_SWEDISH_WEEKDAYS = ("måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag")


def _sek(value: float | Decimal) -> str:
    """Money to the öre with a Swedish decimal comma — '17,90'.

    A truncated '13.9' next to a '13,90 kr/liter' reads as a different price; money is
    always to the öre in this app, and the emails are Swedish user text.
    """
    return f"{float(value):.2f}".replace(".", ",")


def _ranked_store_groups(deals: list[DealRow]) -> list[tuple[str, list[DealRow]]]:
    """Butiker alphabetically; inside each, sure wins by margin, then the uncomparable.

    One section per butik = one leg of the shopping round. The in-store ranking mirrors
    the portal's rankDeals: BEST by the kr/unit margin (biggest win first), then UNKNOWN
    by the store's discount — the only number those rows have.
    """
    groups: dict[str, list[DealRow]] = {}
    for deal in deals:
        groups.setdefault(deal.store_name, []).append(deal)

    def rank(store_deals: list[DealRow]) -> list[DealRow]:
        best = sorted(
            (d for d in store_deals if d.verdict == DEAL_BEST),
            key=lambda d: -(d.savings_per_unit_sek or 0.0),
        )
        unknown = sorted(
            (d for d in store_deals if d.verdict == DEAL_UNKNOWN),
            key=lambda d: -d.discount_percent,
        )
        # Defensive: the scheduler filters WORSE out, but a caller that does not still
        # gets a coherent email rather than silently dropped rows.
        rest = [d for d in store_deals if d.verdict not in (DEAL_BEST, DEAL_UNKNOWN)]
        return best + unknown + rest

    return [(name, rank(store_deals)) for name, store_deals in sorted(groups.items())]


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
        deals: list[DealRow],
        watched_products: list[dict[str, str | Decimal | None]],
    ) -> bool:
        """Send the weekly buy-list email.

        `deals` is the pre-filtered buy list (BEST + UNKNOWN from domain/deals.py) —
        grouping per butik and the in-butik ranking happen here, because they are
        presentation, not judgement.

        Returns:
            True if email was sent successfully.
        """
        subject = "Veckans inköpslista – Prisspaning"
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
                <td style="padding: 8px; border-bottom: 1px solid #eee;">Ditt målpris:</td>
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
                <td style="padding: 8px; border-bottom: 1px solid #eee;">Jämförelsepris:</td>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">
                    <strong>{unit_price_sek} kr/enhet</strong>
                </td>
            </tr>"""

        unit_price_drop_row = ""
        if unit_price_drop_percent is not None:
            unit_price_drop_row = f"""
            <tr>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">Jämförelsepris-fall:</td>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">
                    <strong style="color: #22c55e;">
                        {unit_price_drop_percent:.1f}% under ordinarie jämförelsepris
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
                Detta mail skickades av Prisspaning. Du får detta för att du bevakar produkten.
            </p>
        </body>
        </html>
        """

    def _deal_row_html(self, deal: DealRow, now: datetime) -> str:
        """One buy-list row: product (linked), price + jfr-pris, and the margin that decides.

        A deal seen more than DEAL_STALE_HOURS ago is dated in Swedish civil time
        ("sett fredag 24/7"), never hidden — the campaign may already be over, and
        saying when we looked is the honest version of that. Same rule as the portal's
        muted Sett column.
        """
        safe_name = html.escape(deal.product_name)
        # The email is read standing in the aisle: the product name links straight
        # to the store's page (scheme-validated, like the alert emails).
        if self._is_safe_url(deal.store_url):
            safe_url = html.escape(deal.store_url, quote=True)
            product_cell = f'<a href="{safe_url}" style="color: #2563eb;">{safe_name}</a>'
        else:
            product_cell = safe_name
        if deal.package_size:
            product_cell += (
                f'<br><span style="color: #64748b; font-size: 0.85em;">'
                f"{html.escape(deal.package_size)}</span>"
            )

        # Jfr-pris under the absolute price — the comparable number, when known.
        unit_label = f"kr/{deal.unit}" if deal.unit else "kr/enhet"
        price_cell = f'<strong style="color: #22c55e;">{_sek(deal.offer_price_sek)} kr</strong>'
        if deal.unit_price_sek is not None:
            price_cell += (
                f'<br><span style="color: #64748b; font-size: 0.85em;">'
                f"{_sek(deal.unit_price_sek)} {html.escape(unit_label)}</span>"
            )

        verdict_cell = self._verdict_html(deal, unit_label)
        if now - deal.checked_at > timedelta(hours=DEAL_STALE_HOURS):
            seen_local = deal.checked_at.replace(tzinfo=UTC).astimezone(STORE_TIMEZONE)
            seen_text = (
                f"sett {_SWEDISH_WEEKDAYS[seen_local.weekday()]} "
                f"{seen_local.day}/{seen_local.month} — kan ha hunnit ta slut"
            )
            verdict_cell += (
                f'<br><span style="color: #b45309; font-size: 0.85em;">'
                f"{html.escape(seen_text)}</span>"
            )

        return f"""
                <tr>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">
                        {product_cell}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">
                        {price_cell}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">
                        {verdict_cell}</td>
                </tr>"""

    @staticmethod
    def _verdict_html(deal: DealRow, unit_label: str) -> str:
        """The margin as a sentence — '0,45 kr/l billigare än Willys 24-pack' is what
        you act on; a bare 'Billigast' badge is not."""
        alt = deal.best_alt_store or ""
        if deal.best_alt_package_size:
            alt = f"{alt} {deal.best_alt_package_size}".strip()
        if deal.verdict == DEAL_BEST:
            margin = deal.savings_per_unit_sek or 0.0
            text = (
                f"{_sek(margin)} {unit_label} billigare än {alt}"
                if margin > 0
                else f"lika billigt som {alt}"
            )
            return f'<span style="color: #16a34a;">{html.escape(text)}</span>'
        # UNKNOWN rides with the buyable ones (it is not KNOWN to be bad) but says
        # honestly why no comparison exists — same wording as the portal.
        why = "länken saknar mängd" if deal.unit_price_sek is None else "enda länken för produkten"
        return f'<span style="color: #64748b;">{html.escape(f"kan inte jämföras — {why}")}</span>'

    def _build_summary_html(
        self,
        deals: list[DealRow],
        watched_products: list[dict[str, str | Decimal | None]],
        now: datetime | None = None,
    ) -> str:
        """Build HTML for the weekly buy-list email — one section per butik."""
        if now is None:
            now = datetime.now(UTC).replace(tzinfo=None)

        deals_html = ""
        if deals:
            for store_name, store_deals in _ranked_store_groups(deals):
                rows = "".join(self._deal_row_html(deal, now) for deal in store_deals)
                deals_html += f"""
            <h3 style="color: #1e3a5f; margin-top: 30px;">{html.escape(store_name)}</h3>
            <table style="width: 100%; border-collapse: collapse;">
                <thead>
                    <tr style="background: #f3f4f6;">
                        <th style="padding: 8px; text-align: left;">Produkt</th>
                        <th style="padding: 8px; text-align: left;">Pris</th>
                        <th style="padding: 8px; text-align: left;">Jämförelse</th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>"""
        else:
            deals_html = "<p>Inga köpvärda erbjudanden den här veckan.</p>"

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
                        <th style="padding: 8px; text-align: left;">Lägsta pris</th>
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
            <h2 style="color: #1e3a5f;">Veckans inköpslista</h2>
            <p>Det här är värt att köpa den här veckan — ett avsnitt per butik.
               Jämförelsen är mot produktens billigaste andra länk, per enhet.</p>
            {deals_html}
            {watched_html}

            <hr style="margin-top: 30px; border: none; border-top: 1px solid #eee;">
            <p style="color: #666; font-size: 0.9em;">
                Detta mail skickades av Prisspaning.
            </p>
        </body>
        </html>
        """
