---
phase: 01-skeleton-domain-copy
plan: 03
type: execute
wave: 3
depends_on: [01-02]
files_modified:
  - alembic.ini
  - alembic/env.py
  - alembic/script.py.mako
  - alembic/versions/0001_initial.py
autonomous: true
requirements:
  - DB-01
  - DB-02
  - DB-03
  - DB-04
  - DB-05
user_setup: []
tags:
  - alembic
  - postgres
  - migration
  - squash

must_haves:
  truths:
    - "alembic upgrade head against a fresh Postgres 16 produces exactly 5 tables (stores, products, product_stores, price_points, watches) plus alembic_version (REQ DB-05, ROADMAP gate 2)"
    - "products.tenant_id and watches.tenant_id are NOT NULL UUID columns with NO foreign key (D-03 — no contexts/tenants table; tenant_id is a free-floating UUID)"
    - "Migration squashes the FOUR source migrations (add_price_tracker_tables, add_price_drop_threshold, add_unit_price_alerts, add_doz_store) into one upgrade() body derived from the FINAL state of src/domain/models.py (D-07 — hand-derive, not literal concat)"
    - "Migration uses postgresql.UUID(as_uuid=True) and postgresql.JSONB directly (D-08 — Postgres-only)"
    - "op.bulk_insert seeds the 5 stores (ICA, Willys, Apotea, Med24, Doz) with literal slug, store_type, base_url, parser_config (D-09)"
    - "No tenants table created (D-10 — D-03 explicit non-action)"
    - "alembic/env.py imports Base from infra.db so target_metadata is wired correctly"
  artifacts:
    - path: "alembic.ini"
      provides: "Alembic config with sqlalchemy.url placeholder + script_location = alembic"
      contains: "script_location = alembic"
    - path: "alembic/env.py"
      provides: "Async-aware env with target_metadata = Base.metadata"
      contains: "from infra.db import Base"
    - path: "alembic/versions/0001_initial.py"
      provides: "Squashed initial migration (5 tables + 5 seeded stores)"
      contains: "def upgrade"
  key_links:
    - from: "alembic/env.py"
      to: "src/infra/db.py"
      via: "from infra.db import Base"
      pattern: "infra\\.db"
    - from: "alembic/versions/0001_initial.py"
      to: "src/domain/models.py"
      via: "Schema derived from final models.py state (D-07)"
      pattern: "models\\.py is the source of truth"
    - from: "op.bulk_insert in 0001_initial.py"
      to: "5 store rows (ICA, Willys, Apotea, Med24, Doz)"
      via: "Literal data inline in migration body"
      pattern: "op\\.bulk_insert.*stores"
---

<objective>
Stand up Alembic in this empty repo and write the single initial migration that produces the final 5-table schema (REQ DB-01..DB-05), seeded with 5 stores. The schema is derived from the FINAL state of `src/domain/models.py` (D-07) — NOT a literal concatenation of the four source migrations.

Purpose: Ship gate 2 of Phase 1 — `alembic upgrade head` succeeds against a fresh Postgres and produces exactly the schema the ported domain code expects. Without this, no later phase can run real DB operations.

Output:
- `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako` (Alembic scaffolding)
- `alembic/versions/0001_initial.py` (squashed migration: 5 tables + 5 seeded stores)
- Verification gate: a fresh Postgres container reaches `head` revision and contains the 5 tables with the 5 stores.

Why hand-derived (D-07): the source repo has 4 chronological migrations (initial + price_drop_threshold + unit_price_alerts + add_doz_store) carrying ALTER TABLE history. This repo's Alembic graph is empty — there is no history to preserve, only the final shape. Concatenating ALTER statements would be code-archaeology with zero value. The final shape lives in `src/domain/models.py` and is the single source of truth.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/01-skeleton-domain-copy/01-CONTEXT.md
@.planning/phases/01-skeleton-domain-copy/01-01-skeleton-PLAN.md
@.planning/phases/01-skeleton-domain-copy/01-02-domain-port-PLAN.md
@EXTRACTION.md

<interfaces>
Schema derives from `src/domain/models.py` (Plan 02 output). Final shape per model:

**Table `stores`** (was `price_tracker_stores`)
- `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
- `name VARCHAR(100) NOT NULL`
- `slug VARCHAR(50) UNIQUE NOT NULL` + INDEX
- `store_type VARCHAR(20) NOT NULL`
- `base_url VARCHAR(255) NOT NULL`
- `parser_config JSONB NOT NULL DEFAULT '{}'`
- `is_active BOOLEAN NOT NULL DEFAULT true`
- `created_at TIMESTAMP NOT NULL DEFAULT now()`

**Table `products`** (was `price_tracker_products`)
- `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
- `tenant_id UUID NOT NULL` + INDEX (NO FK per D-03)
- `name VARCHAR(255) NOT NULL`
- `brand VARCHAR(100) NULL`
- `category VARCHAR(100) NULL`
- `unit VARCHAR(50) NULL`
- `package_size VARCHAR(50) NULL` (added later in source — bake in)
- `package_quantity NUMERIC(10,2) NULL` (added later in source — bake in)
- `created_at TIMESTAMP NOT NULL DEFAULT now()`
- `updated_at TIMESTAMP NOT NULL DEFAULT now()`

**Table `product_stores`** (was `price_tracker_product_stores`)
- `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
- `product_id UUID NOT NULL REFERENCES products(id)` + INDEX
- `store_id UUID NOT NULL REFERENCES stores(id)` + INDEX
- `store_url VARCHAR(512) NOT NULL`
- `store_product_id VARCHAR(100) NULL`
- `is_active BOOLEAN NOT NULL DEFAULT true`
- `last_checked_at TIMESTAMP NULL`
- `check_frequency_hours INTEGER NOT NULL DEFAULT 72` (note: source migration default was 24; models.py default is 72 — D-07 says final models.py wins)
- `check_weekday INTEGER NULL` (added later in source — bake in)
- `next_check_at TIMESTAMP NULL` (added later in source — bake in)
- `UNIQUE(product_id, store_id) name uq_product_store`

**Table `price_points`** (was `price_tracker_price_points`)
- `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
- `product_store_id UUID NOT NULL REFERENCES product_stores(id)` + INDEX
- `price_sek NUMERIC(10,2) NOT NULL`
- `unit_price_sek NUMERIC(10,2) NULL`
- `offer_price_sek NUMERIC(10,2) NULL`
- `offer_type VARCHAR(50) NULL`
- `offer_details VARCHAR(255) NULL`
- `in_stock BOOLEAN NOT NULL DEFAULT true`
- `raw_data JSONB NULL`
- `checked_at TIMESTAMP NOT NULL` + INDEX

**Table `watches`** (was `price_tracker_watches`)
- `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
- `tenant_id UUID NOT NULL` + INDEX (NO FK per D-03 — replaces source's context_id FK to contexts.id)
- `product_id UUID NOT NULL REFERENCES products(id)` + INDEX
- `target_price_sek NUMERIC(10,2) NULL`
- `alert_on_any_offer BOOLEAN NOT NULL DEFAULT false`
- `price_drop_threshold_percent INTEGER NULL` (added in source migration #2 — bake in)
- `unit_price_target_sek NUMERIC(10,2) NULL` (added in source migration #3 — bake in)
- `unit_price_drop_threshold_percent INTEGER NULL` (added in source migration #3 — bake in)
- `email_address VARCHAR(255) NOT NULL`
- `is_active BOOLEAN NOT NULL DEFAULT true`
- `last_alerted_at TIMESTAMP NULL`
- `created_at TIMESTAMP NOT NULL DEFAULT now()`

**Five seeded stores** (D-09; same data the source registry implies + Doz from migration #4):

| name | slug | store_type | base_url |
|---|---|---|---|
| ICA | ica | grocery | https://handlaprivatkund.ica.se |
| Willys | willys | grocery | https://www.willys.se |
| Apotea | apotea | pharmacy | https://www.apotea.se |
| Med24 | med24 | pharmacy | https://www.med24.se |
| Doz Apotek | doz | pharmacy | https://www.dozapotek.se |

`parser_config` for all 5 = `{}` (empty JSONB; source uses default).
`is_active` for all 5 = `true`.
`id` populated via `gen_random_uuid()` server-side default — do NOT hardcode UUIDs in seed data (let the DB pick).

The source migration `op.execute("INSERT INTO ...")` style works but is a string SQL; D-09 specifies `op.bulk_insert` with literal Python data — preferred for type-safety + readability.

Source migration files (read-only reference, NOT copied):
- `/home/magnus/dev/ai-agent-platform/services/agent/alembic/versions/20260115_add_price_tracker_tables.py` (218 LOC — main schema + first 4 store seeds)
- `/home/magnus/dev/ai-agent-platform/services/agent/alembic/versions/20260115_add_price_drop_threshold.py` (32 LOC — adds price_drop_threshold_percent to watches)
- `/home/magnus/dev/ai-agent-platform/services/agent/alembic/versions/20260115_add_unit_price_alerts.py` (37 LOC — adds unit_price_target_sek + unit_price_drop_threshold_percent to watches)
- `/home/magnus/dev/ai-agent-platform/services/agent/alembic/versions/20260217_add_doz_store.py` (42 LOC — adds Doz seed + alters next_check_at timezone — the timezone alter is a no-op for our fresh schema since we create next_check_at as TIMESTAMP WITHOUT TIME ZONE from the start)

NOTE on Doz migration: it ALSO does `ALTER TABLE price_tracker_product_stores ALTER COLUMN next_check_at TYPE TIMESTAMP WITHOUT TIME ZONE`. Because our squashed migration creates `next_check_at` as plain `sa.DateTime()` (which maps to `TIMESTAMP WITHOUT TIME ZONE` by default in Postgres), this alter is implicit in the final shape — no explicit op needed.

Postgres connection string for verification (matches Plan 05's docker-compose):
```
postgresql+asyncpg://price_tracker:price_tracker@localhost:5432/price_tracker
```
For Alembic (sync driver): `postgresql+psycopg2://price_tracker:price_tracker@localhost:5432/price_tracker` — but we want async everywhere. Use the async driver in env.py via `async_engine_from_config` + asyncio context, OR use `psycopg` (v3) which supports both sync and async with the same URL. Simpler: env.py uses sync `engine_from_config` with the `psycopg2` URL just for migrations (Alembic CLI is synchronous by default). ALTERNATIVE: the modern Alembic async template — see `alembic init -t async`. **Decision: use the async template** so env.py uses `async_engine_from_config` against `asyncpg://` — keeps the URL identical to runtime. This adds ~25 lines to env.py but avoids dual driver config.
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Initialize Alembic scaffolding</name>
  <files>alembic.ini, alembic/env.py, alembic/script.py.mako</files>
  <read_first>
    - src/infra/db.py (Plan 01 — confirms `Base` is exported)
    - src/domain/models.py (Plan 02 — confirms ORM models registered with Base.metadata)
    - /home/magnus/dev/price-tracker/.planning/phases/01-skeleton-domain-copy/01-CONTEXT.md (D-04, D-08)
    - /home/magnus/dev/ai-agent-platform/services/agent/alembic/env.py (reference for env.py shape)
  </read_first>
  <action>
Run `poetry run alembic init -t async alembic` to scaffold async-template Alembic in the `alembic/` directory at the repo root. This creates `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/` (empty).

Then customize:

**`alembic.ini`** — set:
- `script_location = alembic`
- `sqlalchemy.url = postgresql+asyncpg://price_tracker:price_tracker@localhost:5432/price_tracker` (matches Plan 05 docker-compose service)
- Leave `file_template`, logger sections at template defaults.

**`alembic/env.py`** — modify the auto-generated async template to:
1. Add `import sys; from pathlib import Path; sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))` at the top so the `domain` and `infra` packages resolve when Alembic runs from the repo root.
2. Add `from infra.db import Base` and `import domain.models  # noqa: F401  registers ORM models with Base.metadata`. (The bare import side-effect is necessary so `target_metadata = Base.metadata` includes the price-tracker tables.)
3. Set `target_metadata = Base.metadata`.

Leave the rest (run_migrations_offline / run_migrations_online with async engine) as the template provides.

**`alembic/script.py.mako`** — leave at template default.

**Do NOT** create `alembic/versions/0001_initial.py` here — Task 2 does that.
  </action>
  <verify>
    <automated>poetry run alembic --version &amp;&amp; test -f alembic.ini &amp;&amp; test -f alembic/env.py &amp;&amp; grep -q "from infra.db import Base" alembic/env.py &amp;&amp; grep -q "import domain.models" alembic/env.py &amp;&amp; grep -q "target_metadata = Base.metadata" alembic/env.py &amp;&amp; grep -q "script_location = alembic" alembic.ini &amp;&amp; grep -q "postgresql+asyncpg" alembic.ini</automated>
  </verify>
  <acceptance_criteria>
- `alembic.ini` exists at repo root
- `alembic/env.py` exists; `grep -c 'from infra.db import Base' alembic/env.py` returns 1
- `grep -c 'import domain.models' alembic/env.py` returns 1
- `grep -c 'target_metadata = Base.metadata' alembic/env.py` returns 1
- `alembic/script.py.mako` exists
- `alembic/versions/` directory exists and is empty (Task 2 fills it)
- `grep -c 'postgresql+asyncpg' alembic.ini` returns 1
- `poetry run alembic --version` exits 0
- `poetry run alembic check 2>&1 | head -5` runs (may report "Target database is not up to date" with no DB — that's expected; the command parsed config OK)
  </acceptance_criteria>
  <done>Alembic scaffolded; env.py wired to Base.metadata via the ported ORM models; ready for Task 2 to write the initial migration.</done>
</task>

<task type="auto">
  <name>Task 2: Write squashed initial migration + verify against fresh Postgres</name>
  <files>alembic/versions/0001_initial.py</files>
  <read_first>
    - src/domain/models.py (Plan 02 — final shape is the source of truth per D-07)
    - /home/magnus/dev/ai-agent-platform/services/agent/alembic/versions/20260115_add_price_tracker_tables.py (REFERENCE for op.create_table style)
    - /home/magnus/dev/ai-agent-platform/services/agent/alembic/versions/20260115_add_price_drop_threshold.py (REFERENCE for the price_drop_threshold_percent column)
    - /home/magnus/dev/ai-agent-platform/services/agent/alembic/versions/20260115_add_unit_price_alerts.py (REFERENCE for the two unit-price columns)
    - /home/magnus/dev/ai-agent-platform/services/agent/alembic/versions/20260217_add_doz_store.py (REFERENCE for the Doz seed row)
    - /home/magnus/dev/price-tracker/.planning/phases/01-skeleton-domain-copy/01-CONTEXT.md (D-07, D-08, D-09, D-10)
  </read_first>
  <action>
Write `alembic/versions/0001_initial.py` from scratch (do NOT use `alembic revision --autogenerate` — D-07 says hand-derive). File template:

```python
"""initial schema (squashed: tables + threshold + unit-price alerts + doz seed)

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-02

Squashes four source migrations from ai-agent-platform/services/agent/alembic/versions/:
- 20260115_add_price_tracker_tables (initial schema, 4 store seeds)
- 20260115_add_price_drop_threshold (adds price_drop_threshold_percent to watches)
- 20260115_add_unit_price_alerts   (adds unit_price_target_sek, unit_price_drop_threshold_percent to watches)
- 20260217_add_doz_store           (adds Doz seed; we bake all 5 stores at create time)

Schema derived from the final state of src/domain/models.py per D-07.
Tables drop the price_tracker_ prefix per DB-04.
context_id FK to contexts.id is replaced with a free-floating tenant_id UUID column per D-02/D-03.
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
        sa.UniqueConstraint("slug", name="uq_store_slug"),
    )
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

    # 6. Seed 5 stores via op.bulk_insert (D-09)
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

    # NO tenants table created (D-10 — D-03 explicit non-action; tenant_id is a free-floating UUID).


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
```

Then verify against a fresh Postgres. Two paths — **prefer Path A** (no docker yet because Plan 05 lands docker-compose):

**Path A (recommended for Phase 1 self-contained verification)** — spin up an ephemeral Postgres via the `docker run` one-liner, run upgrade, assert, tear down:

```bash
docker run -d --name pt-pg-verify -e POSTGRES_USER=price_tracker -e POSTGRES_PASSWORD=price_tracker -e POSTGRES_DB=price_tracker -p 5432:5432 postgres:16-alpine
# Wait for ready
until docker exec pt-pg-verify pg_isready -U price_tracker; do sleep 1; done
# Run migration
poetry run alembic upgrade head
# Assert tables
docker exec pt-pg-verify psql -U price_tracker -d price_tracker -c "\dt" | grep -cE "stores|products|product_stores|price_points|watches"
# Assert seeded stores
docker exec pt-pg-verify psql -U price_tracker -d price_tracker -tAc "SELECT slug FROM stores ORDER BY slug;" | tr '\n' ',' 
# expected output: apotea,doz,ica,med24,willys,
# Assert tenant_id columns are NOT NULL UUID with no FK
docker exec pt-pg-verify psql -U price_tracker -d price_tracker -tAc "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name='products' AND column_name='tenant_id';"
docker exec pt-pg-verify psql -U price_tracker -d price_tracker -tAc "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name='watches' AND column_name='tenant_id';"
# Assert NO contexts/tenants table exists
docker exec pt-pg-verify psql -U price_tracker -d price_tracker -tAc "SELECT count(*) FROM information_schema.tables WHERE table_name IN ('contexts','tenants');"
# expected: 0
# Tear down
docker rm -f pt-pg-verify
```

**Path B (skip if Path A works)** — wait for Plan 05's docker-compose to land, then verify there.

Use Path A for in-plan verification. Plan 05's Task will re-run the same verification against the docker-compose-managed Postgres as part of the gate-2 confirmation.
  </action>
  <verify>
    <automated>poetry run alembic check 2>&amp;1 | tee /tmp/alembic-check.log; ! grep -q "Detected" /tmp/alembic-check.log &amp;&amp; docker run -d --rm --name pt-pg-verify -e POSTGRES_USER=price_tracker -e POSTGRES_PASSWORD=price_tracker -e POSTGRES_DB=price_tracker -p 5432:5432 postgres:16-alpine &amp;&amp; (until docker exec pt-pg-verify pg_isready -U price_tracker &gt;/dev/null 2&gt;&amp;1; do sleep 1; done) &amp;&amp; poetry run alembic upgrade head &amp;&amp; TABLES=$(docker exec pt-pg-verify psql -U price_tracker -d price_tracker -tAc "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename IN ('stores','products','product_stores','price_points','watches')" | sort | tr '\n' ',') &amp;&amp; STORES=$(docker exec pt-pg-verify psql -U price_tracker -d price_tracker -tAc "SELECT slug FROM stores ORDER BY slug" | tr '\n' ',') &amp;&amp; CONTEXTS=$(docker exec pt-pg-verify psql -U price_tracker -d price_tracker -tAc "SELECT count(*) FROM information_schema.tables WHERE table_name IN ('contexts','tenants')") &amp;&amp; docker rm -f pt-pg-verify &amp;&amp; test "$TABLES" = "price_points,product_stores,products,stores,watches," &amp;&amp; test "$STORES" = "apotea,doz,ica,med24,willys," &amp;&amp; test "$CONTEXTS" = "0" &amp;&amp; echo "MIGRATION GATE PASS"</automated>
  </verify>
  <acceptance_criteria>
- `alembic/versions/0001_initial.py` exists; `grep -c "revision: str = \"0001_initial\"" alembic/versions/0001_initial.py` returns 1
- `grep -c "def upgrade" alembic/versions/0001_initial.py` returns 1; `grep -c "def downgrade" alembic/versions/0001_initial.py` returns 1
- `grep -cE 'op\.create_table\("(stores|products|product_stores|price_points|watches)"' alembic/versions/0001_initial.py` returns 5
- `grep -c "op.bulk_insert" alembic/versions/0001_initial.py` returns 1
- `grep -cE "'(ica|willys|apotea|med24|doz)'" alembic/versions/0001_initial.py | grep -E "^[5-9]|^[1-9][0-9]"` (at least one occurrence per slug; literal seed data present)
- `grep -c "tenants\|contexts.id" alembic/versions/0001_initial.py` returns 0 (no FK to contexts; no tenants table created — D-03, D-10)
- `grep -c "price_tracker_" alembic/versions/0001_initial.py` returns 0 (DB-04)
- `grep -cE "postgresql\.UUID\(as_uuid=True\)" alembic/versions/0001_initial.py` returns 7 or more (all UUID columns)
- `grep -cE "postgresql\.JSONB" alembic/versions/0001_initial.py` returns 2 or more (parser_config + raw_data)
- The verify automated command exits with `MIGRATION GATE PASS`
- Fresh Postgres after `alembic upgrade head` contains exactly the 5 expected tables (no contexts, no tenants)
- 5 stores seeded (slugs sorted: apotea, doz, ica, med24, willys)
  </acceptance_criteria>
  <done>Squashed migration writes 5 tables in final shape and seeds 5 stores. Gate 2 of Phase 1 (`alembic upgrade head` against fresh Postgres) is green. SUMMARY records the verified table list and seed data.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Migration runner -> Postgres | Migration executes DDL + seed inserts. Trust assumption: the Postgres user (`price_tracker`) has CREATE/INSERT on the `public` schema. In Phase 1 this is local Postgres; production posture (least-privilege migration role vs runtime role) is a future concern. |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-01-08 | Tampering | Migration seed integrity (5 hardcoded store rows) | mitigate | A typo in `slug` (e.g., "willsy") would silently break parser store_hint lookup and Willys API extractor. Acceptance criteria includes a sorted-slug equality assertion (`apotea,doz,ica,med24,willys,`) — if a slug is wrong the gate fails. Severity: low. |
| T-01-09 | Tampering | Migration table-name drift from models.py | mitigate | Acceptance criteria checks `__tablename__` on each model AND the live `pg_tables` listing. Three-way audit: models.py <-> migration <-> live schema. Severity: low. |
| T-01-10 | Information Disclosure | seed `parser_config` JSONB | accept | All 5 rows seeded with empty `{}`. No secrets, no PII. Severity: info. |
| T-01-11 | Denial of Service | `tenant_id` is NOT NULL but has no FK | accept | An app inserting wrong tenant_id (e.g., zero UUID, or a typoed constant) creates orphaned data. Phase 1 mitigates by funneling all inserts through the `DEFAULT_TENANT_ID` constant in `src/domain/tenant.py` — the constant is the contract. Phase 2/3 service code uses it. Severity: low. Document in SUMMARY: "tenant_id discipline is enforced at code level (DEFAULT_TENANT_ID constant), not DB level (no FK), per D-03." |

No high-severity threats in this plan.
</threat_model>

<verification>
After both tasks complete (final pre-commit gate):

```bash
# Alembic config valid + autogen detects no schema drift between migration and models.py
poetry run alembic check 2>&1
# Fresh Postgres test (matches Task 2 verify command)
docker run -d --rm --name pt-pg-final -e POSTGRES_USER=price_tracker -e POSTGRES_PASSWORD=price_tracker -e POSTGRES_DB=price_tracker -p 5432:5432 postgres:16-alpine
until docker exec pt-pg-final pg_isready -U price_tracker >/dev/null 2>&1; do sleep 1; done
poetry run alembic upgrade head
docker exec pt-pg-final psql -U price_tracker -d price_tracker -c "\dt"
docker exec pt-pg-final psql -U price_tracker -d price_tracker -c "SELECT slug, store_type, base_url FROM stores ORDER BY slug;"
docker rm -f pt-pg-final
```

Expected `\dt` output (sorted):
```
 alembic_version
 price_points
 product_stores
 products
 stores
 watches
```

Expected `SELECT slug ...` output:
```
 apotea | pharmacy | https://www.apotea.se
 doz    | pharmacy | https://www.dozapotek.se
 ica    | grocery  | https://handlaprivatkund.ica.se
 med24  | pharmacy | https://www.med24.se
 willys | grocery  | https://www.willys.se
```
</verification>

<success_criteria>
- `alembic upgrade head` against fresh Postgres 16 succeeds (REQ DB-05, ROADMAP gate 2)
- Exactly 5 domain tables created (stores, products, product_stores, price_points, watches) plus alembic_version
- products.tenant_id and watches.tenant_id are NOT NULL UUID columns with NO foreign key (D-03, REQ DB-02)
- 5 stores seeded with slugs apotea, doz, ica, med24, willys (REQ DB-03)
- No `price_tracker_` table prefix anywhere in migration body (REQ DB-04)
- No `contexts` or `tenants` table created (D-10)
- `alembic check` reports no drift between migration and Base.metadata
</success_criteria>

<output>
After completion, create `.planning/phases/01-skeleton-domain-copy/01-03-SUMMARY.md` containing:
- Final list of tables created (post-upgrade `\dt` output)
- The 5 seeded store rows (slug, store_type, base_url, parser_config, is_active)
- Confirmation that `tenant_id` columns are NOT NULL UUID with no FK
- Confirmation that no `contexts` / `tenants` table was created
- Any unexpected drift `alembic check` reported (and how it was resolved)
- The decision record: hand-derived from final models.py state (D-07), NOT a literal concat of the 4 source migrations
</output>
