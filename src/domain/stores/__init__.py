"""Store-specific parsing configurations."""


def get_store_hints() -> dict[str, str]:
    """Get parsing hints for all stores."""
    return {
        "ica": ICA_HINTS,
        "willys": WILLYS_HINTS,
        "apotea": APOTEA_HINTS,
        "med24": MED24_HINTS,
        "doz": DOZ_HINTS,
        "kronans": KRONANS_HINTS,
        "apohem": APOHEM_HINTS,
        "rusta": RUSTA_HINTS,
        "clasohlson": CLASOHLSON_HINTS,
        "lyko": LYKO_HINTS,
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

APOHEM_HINTS = """
Apohem (pharmacy) uses these patterns:
- Price shown as "X kr"
- "Nice Price" label marks discounted/competitive prices (the current price)
- "Ordinarie pris" for regular price when discounted
- "Finns i lager" for in stock, with delivery estimate ("hos dig inom 1-2 vardagar")
- "Forpackningsstorlek" shows package size (e.g. "132 st")
- Discount codes advertised in banners (e.g. "Anvand kod: X for 10% rabatt") - ignore these
- Prescription items marked "Receptbelagt"
"""

KRONANS_HINTS = """
Kronans Apotek (pharmacy) uses these patterns:
- Price shown as "X kr" (comma decimals, e.g. "102,75 kr")
- "Kampanjpris Online" for online campaign/sale prices, with "Galler t.o.m. <date>"
- "Lagsta pris de senaste 30 dagarna" shows the 30-day low next to campaign prices
- "Ord. pris" for regular price (strikethrough when on sale)
- Comparison price shown as "Jamforpris X kr / ST"
- "Finns i webblager" for in stock; "Tillfalligt slut" for out of stock
- "Klubbpris" for member/loyalty prices
- Prescription items marked "Receptbelagt"
"""

RUSTA_HINTS = """
Rusta (retail chain) uses these patterns:
- Regular price shown as "X kr" or "X:-"
- On sale the regular price shows as "Ordinarie pris X kr" and "(X:-)" beside the
  campaign price - the LARGER value is the ordinarie, the smaller is what you pay
- Comparison price shown as "Jamforpris X kr /liter" (or /kg, /st)
- Club Rusta member prices marked "Club Rusta"
- Some items are sold in-store only ("Kop i varuhus") but still show a price
- Stock wording: "Finns i lager", "Slut i lager"
"""

CLASOHLSON_HINTS = """
Clas Ohlson (retail chain) uses these patterns:
- Regular price shown as "X kr" or "179,90" (comma decimals, non-breaking-space
  thousands separator: "1 290,00")
- On sale BOTH prices show side by side - the struck-through larger value is the
  ordinarie, the smaller is what you pay now
- Comparison price in parentheses right after the price: "(1,91/st)" or "(X/l)"
- Club Clas member prices marked "Klubbpris"
- "Artikelnr: XX-XXXX" identifies the product
- Out of stock: "Slut i onlinelagret" / "Ej i lager"
"""

LYKO_HINTS = """
Lyko (beauty/hygiene webshop) uses these patterns:
- Price shown as "X kr" (comma decimals, e.g. "104,25 kr")
- On sale the previous price shows struck through as "Tid. pris X kr" or
  "Utan kampanj X kr" - the LARGER value is the ordinarie, the smaller is what you pay
- "Rek. pris" is the manufacturer's recommended price, NOT a campaign - a product
  showing only "Rek. pris" is at its ordinary price and is NOT on sale
- "Utan paketpris" shows what a gift set's parts cost separately - also not a campaign
- No comparison price (jamforpris) is printed anywhere on the site
- Package size stated in the title and as a separate size field ("500 ml", "10 st")
- Member prices marked "Lyko Club"
- Stock wording: "Kop", "Bevaka" or "Slut i lager" (physical-store availability is
  shown separately and says nothing about the webshop)
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
