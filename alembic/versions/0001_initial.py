"""initial schema (squashed: tables + threshold + unit-price alerts + doz seed)

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-02

Squashes four source migrations from ai-agent-platform/services/agent/alembic/versions/:
- 20260115 add tables (initial schema, 4 store seeds)
- 20260115 add price drop threshold (adds price_drop_threshold_percent to watches)
- 20260115 add unit price alerts (adds unit_price_target_sek, unit_price_drop_threshold_percent to watches)
- 20260217 add doz store (adds Doz seed; we bake all 5 stores at create time)

Schema derived from the final state of src/domain/models.py per D-07.
Source-prefix dropped per DB-04 (table names are bare: stores, products, etc.).
context_id FK to the source platform's context table is replaced with a
free-floating tenant_id UUID column per D-02/D-03 (no FK, no separate table).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. stores (no tenant scope; shared registry)
    op.create_table(
        "stores",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(50), nullable=False),
        sa.Column("store_type", sa.String(20), nullable=False),
        sa.Column("base_url", sa.String(255), nullable=False),
        sa.Column("parser_config", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # slug uniqueness is enforced by the unique index below — matches what
    # ORM `mapped_column(String(50), unique=True, index=True)` produces.
    op.create_index(op.f("ix_stores_slug"), "stores", ["slug"], unique=True)

    # 2. products (tenant-scoped via tenant_id; NO FK per D-03)
    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("brand", sa.String(100), nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("unit", sa.String(50), nullable=True),
        sa.Column("package_size", sa.String(50), nullable=True),
        sa.Column("package_quantity", sa.Numeric(10, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_products_tenant_id"), "products", ["tenant_id"], unique=False)

    # 3. product_stores (M2M with metadata; cascading delete on product side preserved)
    op.create_table(
        "product_stores",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_url", sa.String(512), nullable=False),
        sa.Column("store_product_id", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("check_frequency_hours", sa.Integer(), server_default="72", nullable=False),
        sa.Column("check_weekday", sa.Integer(), nullable=True),
        sa.Column("next_check_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "store_id", name="uq_product_store"),
    )
    op.create_index(op.f("ix_product_stores_product_id"), "product_stores", ["product_id"], unique=False)
    op.create_index(op.f("ix_product_stores_store_id"), "product_stores", ["store_id"], unique=False)

    # 4. price_points (history table)
    op.create_table(
        "price_points",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("product_store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("price_sek", sa.Numeric(10, 2), nullable=False),
        sa.Column("unit_price_sek", sa.Numeric(10, 2), nullable=True),
        sa.Column("offer_price_sek", sa.Numeric(10, 2), nullable=True),
        sa.Column("offer_type", sa.String(50), nullable=True),
        sa.Column("offer_details", sa.String(255), nullable=True),
        sa.Column("in_stock", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["product_store_id"], ["product_stores.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_price_points_product_store_id"), "price_points", ["product_store_id"], unique=False)
    op.create_index(op.f("ix_price_points_checked_at"), "price_points", ["checked_at"], unique=False)

    # 5. watches (tenant-scoped via tenant_id; NO FK per D-03; bakes in price_drop_threshold + unit-price columns)
    op.create_table(
        "watches",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_price_sek", sa.Numeric(10, 2), nullable=True),
        sa.Column("alert_on_any_offer", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("price_drop_threshold_percent", sa.Integer(), nullable=True),
        sa.Column("unit_price_target_sek", sa.Numeric(10, 2), nullable=True),
        sa.Column("unit_price_drop_threshold_percent", sa.Integer(), nullable=True),
        sa.Column("email_address", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_alerted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_watches_tenant_id"), "watches", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_watches_product_id"), "watches", ["product_id"], unique=False)

    # 6. Seed 5 stores via bulk insert (D-09)
    stores_table = sa.table(
        "stores",
        sa.column("name", sa.String),
        sa.column("slug", sa.String),
        sa.column("store_type", sa.String),
        sa.column("base_url", sa.String),
        sa.column("parser_config", postgresql.JSONB),
        sa.column("is_active", sa.Boolean),
    )
    op.bulk_insert(
        stores_table,
        [
            {"name": "ICA",        "slug": "ica",     "store_type": "grocery",  "base_url": "https://handlaprivatkund.ica.se", "parser_config": {}, "is_active": True},
            {"name": "Willys",     "slug": "willys",  "store_type": "grocery",  "base_url": "https://www.willys.se",           "parser_config": {}, "is_active": True},
            {"name": "Apotea",     "slug": "apotea",  "store_type": "pharmacy", "base_url": "https://www.apotea.se",           "parser_config": {}, "is_active": True},
            {"name": "Med24",      "slug": "med24",   "store_type": "pharmacy", "base_url": "https://www.med24.se",            "parser_config": {}, "is_active": True},
            {"name": "Doz Apotek", "slug": "doz",     "store_type": "pharmacy", "base_url": "https://www.dozapotek.se",        "parser_config": {}, "is_active": True},
        ],
    )

    # NO separate tenant table created (D-10 — D-03 explicit non-action; tenant_id is a free-floating UUID).


def downgrade() -> None:
    op.drop_index(op.f("ix_watches_product_id"), table_name="watches")
    op.drop_index(op.f("ix_watches_tenant_id"), table_name="watches")
    op.drop_table("watches")

    op.drop_index(op.f("ix_price_points_checked_at"), table_name="price_points")
    op.drop_index(op.f("ix_price_points_product_store_id"), table_name="price_points")
    op.drop_table("price_points")

    op.drop_index(op.f("ix_product_stores_store_id"), table_name="product_stores")
    op.drop_index(op.f("ix_product_stores_product_id"), table_name="product_stores")
    op.drop_table("product_stores")

    op.drop_index(op.f("ix_products_tenant_id"), table_name="products")
    op.drop_table("products")

    op.drop_index(op.f("ix_stores_slug"), table_name="stores")
    op.drop_table("stores")
