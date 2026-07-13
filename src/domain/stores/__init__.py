"""Store-specific parsing configurations."""


def get_store_hints() -> dict[str, str]:
    """Get parsing hints for all stores."""
    return {
        "ica": ICA_HINTS,
        "willys": WILLYS_HINTS,
        "apotea": APOTEA_HINTS,
        "med24": MED24_HINTS,
        "doz": DOZ_HINTS,
    }


ICA_HINTS = """
ICA Handla uses these patterns:
- Regular price shown as "X kr" or "X:-"
- Comparison price (jmfpris) shown as "X kr/kg", "X kr/l", or "X kr/st" (per piece)
- For multi-packs (toilet paper, diapers), unit price is "kr/st" (per roll/piece)
- "Stammispris" for loyalty member prices (orange/yellow highlighting)
- "Veckans erbjudande" for weekly deals
- Out of stock shown as "Tillfalligt slut" or "Ej tillganglig"
- Multi-buy offers like "2 for X kr" or "Kop 3 betala for 2"
"""

WILLYS_HINTS = """
Willys uses these patterns:
- Regular price as "X kr" or "X,XX"
- "Jmfrpris" for comparison pricing (kr/kg, kr/l, or kr/st for multi-packs)
- For multi-pack products (toilet paper, etc.), unit price is shown as "kr/st" (per piece)
- "Extrapris" for discounted items (red highlighting)
- "Veckans klipp" for weekly specials
- Stock status shown near add-to-cart button
- Loyalty prices shown as "Willys Plus-pris"
"""

APOTEA_HINTS = """
Apotea (pharmacy) uses these patterns:
- "Pris" for current price
- "Ord.pris" for original price (strikethrough when on sale)
- "Nu" or "Just nu" for sale prices
- "Spara X kr" shows discount amount
- "Tillfallet slut" for out of stock
- Prescription items marked "Receptbelagt"
"""

MED24_HINTS = """
Med24 (pharmacy) uses these patterns:
- Price shown as "X kr"
- "Spara X kr" badges for discounts
- Green badges indicate offers
- "Lagerstatus" indicates availability
- "Ordinarie pris" for regular price
- Campaign prices often in larger font
"""

DOZ_HINTS = """
Doz Apotek (pharmacy) uses these patterns:
- Price shown as "X kr" or "X,- kr"
- "Ordinarie pris" for regular price
- "Kampanjpris" for campaign/sale price
- "Spara X kr" for discount amount
- "Tillfallet slut" or "Ej i lager" for out of stock
- Prescription items marked with "Receptbelagt"
- Comparison price shown as "X kr/st" or "X kr/dos"
"""
