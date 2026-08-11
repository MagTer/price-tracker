"""Price tracker notifications using platform email service."""

from __future__ import annotations

import html
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from domain.deals import (
    DEAL_BEST,
    DEAL_STALE_HOURS,
    DEAL_UNKNOWN,
    DEAL_WORSE,
    PRICE_LOW_WINDOW_DAYS,
    TIMING_POOR,
    DealRow,
)
from domain.protocols.email import EmailMessage, IEmailService
from domain.schedule import STORE_TIMEZONE

logger = logging.getLogger(__name__)

_SWEDISH_WEEKDAYS = ("måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag")

# The span window, said in the unit the reader thinks in. Derived, never written down: it
# IS the floor window, and a "12 veckor" that stopped matching PRICE_LOW_WINDOW_DAYS would
# be a claim about a period we did not look at.
_SPAN_WEEKS = PRICE_LOW_WINDOW_DAYS // 7

# At or within a hair of the floor — the same 2 % the portal's bar uses to promote the note
# from "nära lägsta" to "lägsta noterade": below it, quantity rounding alone can move the
# figure, so claiming a new record would be arithmetic noise wearing a superlative.
_SPAN_AT_LOW = 0.02
# The lower third of the observed span reads as "cheap"; above it the honest thing is the
# distance to the floor, in kronor.
_SPAN_NEAR_LOW = 1 / 3

# The portal's twin is dealSpanHtml() in api/templates/admin.html. Both READ the deal row's
# own lowest/highest (deals.observed_spans, 84 days) — there is no second query here, only a
# second rendering, because email HTML cannot share a stylesheet with the portal. Keep the
# refusals and the wording in step; they are the same three sentences.


def _muted(text: str) -> str:
    """A reason where a bar would have been — the same weight the portal gives it."""
    return f'<span style="color: #64748b; font-size: 11px;">{html.escape(text)}</span>'


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
        data_quality: dict[str, Any] | None = None,
    ) -> bool:
        """Send the weekly buy-list email.

        `deals` is the pre-filtered buy list (BEST + UNKNOWN, and not a POOR moment, both
        from domain/deals.py) —
        grouping per butik and the in-butik ranking happen here, because they are
        presentation, not judgement. `data_quality` is domain/validation.py's judgement,
        rendered only when something is wrong — a green validator earns no inbox space.

        Returns:
            True if email was sent successfully.
        """
        subject = "Veckans inköpslista – Prisspaning"
        html_body = self._build_summary_html(deals, watched_products, data_quality=data_quality)

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
        # The offer's CONDITION, in the price cell like the portal (v0.41.2): a
        # "Välj & blanda! 2 för 99,00" price is only real if you buy two, and a buy
        # list that hides that sends the reader to the shelf with a false verdict.
        # A type beyond plain kampanj (stammispris, manuellt pris) is a condition too.
        condition_bits: list[str] = []
        if deal.offer_type not in ("kampanj", "erbjudande"):
            condition_bits.append(deal.offer_type)
        if deal.offer_details:
            condition_bits.append(deal.offer_details)
        if condition_bits:
            price_cell += (
                f'<br><span style="color: #b45309; font-size: 0.85em;">'
                f"{html.escape(' · '.join(condition_bits))}</span>"
            )

        # The span bar leads the cell; the sentences under it are the exceptions — why the
        # row is not a plain buy, and the caveats (poor moment, stale, out of stock).
        span_cell = self._span_html(deal, unit_label)
        verdict = self._verdict_html(deal, unit_label)
        verdict_cell = span_cell
        if verdict:
            verdict_cell += f'<div style="margin-top: 5px;">{verdict}</div>'
        # The MOMENT, when it is a poor one. The scheduler filters these out of the weekly
        # buy list, so this is the same defensive honesty the WORSE wording gets: a caller
        # that does not filter must not have the row read as an unqualified recommendation.
        if deal.timing == TIMING_POOR and deal.seen_cheaper_pct is not None:
            seen_text = f"har varit {round(deal.seen_cheaper_pct)} % billigare"
            if deal.lowest_unit_price_sek is not None:
                seen_text += f" — {_sek(deal.lowest_unit_price_sek)} {unit_label}"
            if deal.lowest_store:
                seen_text += f" hos {deal.lowest_store}"
            verdict_cell += (
                f'<br><span style="color: #b45309; font-size: 0.85em;">'
                f"{html.escape(seen_text)}</span>"
            )
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
        # The store's own stock word on the latest point — marked, never hidden (the
        # stale-row rule): the row may still be worth the trip, but not unannounced.
        if deal.in_stock is False:
            verdict_cell += (
                '<br><span style="color: #b45309; font-size: 0.85em;">'
                "butiken anger slut i lager</span>"
            )

        return f"""
                <tr>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;
                        vertical-align: top;">{product_cell}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;
                        vertical-align: top;">{price_cell}</td>
                    <td width="40%" style="padding: 8px; border-bottom: 1px solid #eee;
                        vertical-align: top; width: 40%;">{verdict_cell}</td>
                </tr>"""

    @staticmethod
    def _span_html(deal: DealRow, unit_label: str) -> str:
        """Where this price sits between the product's own lowest and highest observed
        kr/enhet — the column that replaced "0,45 kr/l billigare än Willys 24-pack".

        That sentence answered the wrong question for a buy list. Cheaper than the next
        link says nothing about whether either price is any good: both can sit at the top
        of a span the product has spent twelve weeks below, and the row still reads as a
        win. The span answers what the reader actually stands in the aisle asking — is
        this a good price for THIS product — and it needs no second store to do it.

        Built from tables and percentage widths, not absolute positioning: Outlook renders
        HTML through Word, which has no `position`, and a bar that collapses there would
        take the row's only judgement with it. The fill's right edge IS the marker (the
        portal draws a tick at the same place); a bar in an email cannot afford a second
        overlapping element to say the same thing.

        Refuses in exactly the cases the portal refuses, and says which: no jfr-pris, one
        observation, or a price that has not moved. A bar drawn anyway would claim a
        position inside a range that does not exist.
        """
        now_price = deal.unit_price_sek
        low = deal.lowest_unit_price_sek
        high = deal.highest_unit_price_sek
        if now_price is None:
            return _muted("inget jfr-pris — länken saknar mängd")
        if low is None or high is None:
            return _muted("en observation — inget spann än")
        if high <= low:
            return _muted("priset har inte rört sig — inget spann än")

        pos = min(1.0, max(0.0, (now_price - low) / (high - low)))
        is_low = pos <= _SPAN_NEAR_LOW
        fill_color = "#10b981" if is_low else "#2563eb"
        fill_pct = round(pos * 100, 1)

        cell = "height: 8px; line-height: 8px; font-size: 0;"
        if fill_pct <= 0:
            bar_cells = f'<td style="{cell} background: #eef1f5;">&nbsp;</td>'
        elif fill_pct >= 100:
            bar_cells = f'<td style="{cell} background: {fill_color};">&nbsp;</td>'
        else:
            bar_cells = (
                f'<td width="{fill_pct}%" style="{cell} width: {fill_pct}%; '
                f'background: {fill_color};">&nbsp;</td>'
                f'<td width="{100 - fill_pct}%" style="{cell} width: {100 - fill_pct}%; '
                f'background: #eef1f5;">&nbsp;</td>'
            )

        if pos <= _SPAN_AT_LOW:
            note, note_color = f"lägsta noterade på {_SPAN_WEEKS} veckor", "#047857"
        elif is_low:
            note, note_color = "nära lägsta noterade", "#047857"
        elif deal.timing == TIMING_POOR:
            # The poor-moment line below carries the distance with its butik and date;
            # two sentences saying "har varit billigare" would be one too many.
            note, note_color = "", ""
        else:
            note = f"har varit {_sek(now_price - low)} {unit_label} billigare"
            note_color = "#64748b"

        ends = (
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0"'
            ' style="width: 100%; border-collapse: collapse;"><tr>'
            f'<td style="font-size: 11px; color: #94a3b8; text-align: left;">{_sek(low)}</td>'
            f'<td style="font-size: 11px; color: #94a3b8; text-align: right;">{_sek(high)}</td>'
            "</tr></table>"
        )
        bar = (
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0"'
            ' style="width: 100%; border-collapse: collapse; margin-top: 4px;">'
            f"<tr>{bar_cells}</tr></table>"
        )
        note_html = (
            f'<div style="font-size: 11px; color: {note_color}; margin-top: 5px;">'
            f"{html.escape(note)}</div>"
            if note
            else ""
        )
        return ends + bar + note_html

    @staticmethod
    def _verdict_html(deal: DealRow, unit_label: str) -> str:
        """Why the row is NOT a plain buy, when it is not — and nothing at all when it is.

        A BEST row says nothing here (v0.53.2): every row in this list is best of its
        product's links by construction, so the sentence restated the section it stood in
        while the span bar above it answers the question worth asking. The margin still
        ORDERS each butik's section (_ranked_store_groups) — it is the sort key, not a line
        of text. Same change the portal made, and for the same reason.
        """
        alt = deal.best_alt_store or ""
        if deal.best_alt_package_size:
            alt = f"{alt} {deal.best_alt_package_size}".strip()
        if deal.verdict == DEAL_BEST:
            return ""
        if deal.verdict == DEAL_WORSE:
            # The scheduler filters WORSE out of the weekly email, but _ranked_store_groups
            # keeps them defensively for any caller that does not — and that caller must
            # get the honest sentence, not the UNKNOWN wording for a row that IS comparable.
            margin = abs(deal.savings_per_unit_sek or 0.0)
            text = f"{_sek(margin)} {unit_label} dyrare än {alt}"
            return f'<span style="color: #b45309;">{html.escape(text)}</span>'
        # UNKNOWN rides with the buyable ones (it is not KNOWN to be bad) but says
        # honestly why no comparison exists. NOT "enda länken": best_alt is also None when
        # sibling links exist but none is comparable (no mängd), and that wording hid the
        # actual fix. When the link itself has no mängd the SPAN cell above has already
        # said exactly that and named the same fix — one sentence, not two.
        if deal.unit_price_sek is None:
            return ""
        return '<span style="color: #64748b;">kan inte jämföras — ingen annan jämförbar länk</span>'

    def _build_summary_html(
        self,
        deals: list[DealRow],
        watched_products: list[dict[str, str | Decimal | None]],
        now: datetime | None = None,
        data_quality: dict[str, Any] | None = None,
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
                        <th style="padding: 8px; text-align: left;">Prisläge i eget spann</th>
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
                lowest_price = product.get("lowest_price")
                store_name = product.get("store_name", "")
                # "kr/st"-style when the row carries a computed kr/enhet, plain "kr"
                # for the absolute-price fallback (and for rows without a label).
                price_label = product.get("price_label") or "kr"

                # A watch created before the first successful check has no price yet —
                # an em dash, not the raw None ("None kr") the f-string used to print.
                # Absence is not a number; same rule as the statistics page.
                if isinstance(lowest_price, Decimal | float | int):
                    price_cell = f"{_sek(lowest_price)} {html.escape(str(price_label))}"
                else:
                    price_cell = "&mdash;"

                # Escape all user-controlled data
                safe_name = html.escape(str(name))
                safe_store_name = html.escape(str(store_name))

                watched_rows += f"""
                <tr>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">
                        {safe_name}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">
                        {price_cell}</td>
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

        # Data quality rides along ONLY when something is wrong: a tier regression means
        # a store's campaigns went dark while every check still reads "ok", and a
        # jämförpris mismatch means a recorded price or amount contradicts the store's
        # own printed figure — both are exactly the silent failures a Monday reader can
        # act on, and neither is visible anywhere else outside the Statistik page.
        quality_html = ""
        if data_quality:
            quality_lines = []
            for row in data_quality.get("tier_regressions", []):
                quality_lines.append(
                    f"{html.escape(str(row.get('product_name', '')))} hos "
                    f"{html.escape(str(row.get('store_name', '')))} läses inte längre via "
                    f"{html.escape(str(row.get('expected_source', '')))} — kampanjer kan "
                    f"vara osynliga"
                )
            for row in data_quality.get("unit_price_mismatches", []):
                unit = str(row.get("unit") or "")
                unit_label = f" kr/{html.escape(unit)}" if unit else " kr"
                quality_lines.append(
                    f"{html.escape(str(row.get('product_name', '')))} hos "
                    f"{html.escape(str(row.get('store_name', '')))}: butiken trycker "
                    f"{row.get('printed_unit_price_sek')}{unit_label} men vi räknar fram "
                    f"{row.get('computed_unit_price_sek')}{unit_label} — pris eller mängd "
                    f"är fel"
                )
            if quality_lines:
                items = "".join(f"<li>{line}</li>" for line in quality_lines)
                quality_html = f"""
            <h3 style="color: #b45309; margin-top: 30px;">Datakvalitet — behöver en titt</h3>
            <ul style="color: #444;">{items}</ul>"""

        return f"""
        <!DOCTYPE html>
        <html lang="sv">
        <head><meta charset="UTF-8"></head>
        <body style="font-family: Arial, sans-serif; max-width: 600px;
                     margin: 0 auto; padding: 20px;">
            <h2 style="color: #1e3a5f;">Veckans inköpslista</h2>
            <p>Det här är värt att köpa den här veckan — ett avsnitt per butik.
               Stapeln visar var priset ligger mellan det lägsta och det högsta
               jämförpriset produkten noterats till de senaste {_SPAN_WEEKS} veckorna.</p>
            {deals_html}
            {watched_html}
            {quality_html}

            <hr style="margin-top: 30px; border: none; border-top: 1px solid #eee;">
            <p style="color: #666; font-size: 0.9em;">
                Detta mail skickades av Prisspaning.
            </p>
        </body>
        </html>
        """
