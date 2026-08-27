"""Every store checks Monday and Friday — the days the buy-list email goes out.

Until now ICA checked Mondays ``[0]``, Willys Mondays and Fridays ``[0, 4]``, and every
other store ran in interval mode at the 72h default. The Monday email then made ONE claim
about observations of very different ages. Measured on prod 2026-08-27, over the five
Mondays to 2026-08-24 at 12:00 local: a weekday-mode link's latest observation was 3-5 h
old, an interval-mode link's averaged 12-46 h and was over 48 h in 20 of 56 link-Mondays
(36 %). That is not bad luck, it is the period — 72 h and 7 days are coprime, so an
interval link's check walks around the week and one Monday in three it lands two days out.

It shipped a wrong buy list on 2026-08-24: four Rusta campaign rows observed Saturday 22/8
(99,50 / 36,50 / 36,50 / 19,90 in ``price_points``), all gone by the Tuesday check, all in
Monday's email under the honest "sett lördag - kan ha hunnit ta slut" caveat. The caveat
was true and the row was dead.

Mon+Fri everywhere puts every link's observation inside the same förmiddag window as the
mail that reports it (``schedule.summary_weekdays`` derives the mail's days from THIS
column, so the two cannot drift apart again), and gives a week-long Rusta rea two samples
instead of a drifting one.

**ICA doubles from 68 to 136 checks a week** and that is the cost to watch: it is the same
stratified förmiddag pattern ICA already takes on Mondays, now twice, but it is the store
with the WAF. The measurement to judge it by is ``check_attempts.outcome = 'blocked'``
(5 blocked rows in the 395 checks from 2026-07-27 to 2026-08-13, four of them on one day),
never ``docker logs``, which starts empty at every deploy.

``next_check_at`` is deliberately NOT rewritten: a link keeps its pending slot and lands on
the new schedule after its next completed check, i.e. within 72 h. Link-level overrides in
``product_stores.check_weekdays`` are untouched - those are per-link operator intent, and
this revision is about the store defaults they override.

Data-only revision: no DDL, so ``alembic check`` drift detection is unaffected.

Revision ID: 0013_store_weekdays_mon_fri
Revises: 0012_seed_jysk
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_store_weekdays_mon_fri"
down_revision: str | None = "0012_seed_jysk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0=Monday. Mon+Fri for every store, whatever it ran before.
_WEEKDAYS: list[int] = [0, 4]

# What the chains ran before this revision, for the downgrade: everything absent went
# interval mode (NULL) at the 72h server default.
_PREVIOUS_WEEKDAYS: dict[str, list[int]] = {"ica": [0], "willys": [0, 4]}


def upgrade() -> None:
    op.execute(
        sa.text("UPDATE stores SET check_weekdays = :days").bindparams(
            sa.bindparam("days", _WEEKDAYS, type_=postgresql.JSONB())
        )
    )


def downgrade() -> None:
    op.execute(sa.text("UPDATE stores SET check_weekdays = NULL"))
    for slug, weekdays in _PREVIOUS_WEEKDAYS.items():
        op.execute(
            sa.text("UPDATE stores SET check_weekdays = :days WHERE slug = :slug").bindparams(
                sa.bindparam("days", weekdays, type_=postgresql.JSONB()),
                sa.bindparam("slug", slug),
            )
        )
