"""Add product_stores.store_label — per-link display name for chains with per-butik pricing.

ICA prices per physical butik (Maxi Sandviken vs Supermarket Björksätra), and both butiker
share the single chain-level Store row whose slug drives the extractors. The label lives on
the LINK and disambiguates them everywhere a store name is displayed; NULL means "use the
chain name" (domain.models.link_store_name).

This is the first migration AFTER the in-place-rewritten 0001 (see CLAUDE.md Gotcha 3).
Being a real additive revision, it applies cleanly with `alembic upgrade head` on a database
stamped at 0001_initial — no volume drop, no data loss.

Revision ID: 0002_store_label
Revises: 0001_initial
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_store_label"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "product_stores",
        sa.Column("store_label", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("product_stores", "store_label")
