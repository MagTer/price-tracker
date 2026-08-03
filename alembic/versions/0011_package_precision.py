"""Widen package_quantity / scraped_package_quantity to Numeric(12, 4).

Amounts live in the product's canonical unit (st / liter / kg), and Numeric(10, 2) in the
kg scale is 10-gram resolution: a 24 g sachet could only be stored as 0.02 kg — a 20 %
kr/kg error the UI could not even correct, because Postgres rounds the honest value back
to two decimals on write. Found by the v0.44.0 validator's jämförpris cross-check (Fresh
Island Dippmix: printed 370.83 kr/kg against computed 445.00). Four decimals holds 0.1 g
/ 0.1 ml — small enough for saffron (0.5 g = 0.0005 kg).

Pure type widening: every existing value survives unchanged, and the downgrade narrows
back (rounding any 4-decimal values, which is the pre-v0.45.0 behaviour by definition).

Revision ID: 0011_package_precision
Revises: 0010_seed_lyko
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_package_precision"
down_revision: str | None = "0010_seed_lyko"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Widen both package-amount columns to Numeric(12, 4)."""
    op.alter_column(
        "product_stores",
        "package_quantity",
        type_=sa.Numeric(12, 4),
        existing_type=sa.Numeric(10, 2),
        existing_nullable=True,
    )
    op.alter_column(
        "product_stores",
        "scraped_package_quantity",
        type_=sa.Numeric(12, 4),
        existing_type=sa.Numeric(10, 2),
        existing_nullable=True,
    )


def downgrade() -> None:
    """Narrow back to Numeric(10, 2) — values round to two decimals."""
    op.alter_column(
        "product_stores",
        "scraped_package_quantity",
        type_=sa.Numeric(10, 2),
        existing_type=sa.Numeric(12, 4),
        existing_nullable=True,
    )
    op.alter_column(
        "product_stores",
        "package_quantity",
        type_=sa.Numeric(10, 2),
        existing_type=sa.Numeric(12, 4),
        existing_nullable=True,
    )
