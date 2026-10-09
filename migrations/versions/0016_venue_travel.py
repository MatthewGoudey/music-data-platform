"""venue travel times: minutes by CTA and on foot from home

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-09

ADR 0008 amendment (2026-10-09). `musicdata venues travel` fills these from the Google
Routes API for venues that have none yet; the home address lives in settings, never in
the database or the repo. Transit times are for a Friday-evening departure.
"""

from __future__ import annotations

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE venue
            ADD COLUMN transit_min        INTEGER,
            ADD COLUMN walk_min           INTEGER,
            ADD COLUMN walk_km            NUMERIC(6, 2),
            ADD COLUMN travel_checked_at  TIMESTAMPTZ,
            ADD COLUMN travel_note        TEXT;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE venue
            DROP COLUMN transit_min, DROP COLUMN walk_min, DROP COLUMN walk_km,
            DROP COLUMN travel_checked_at, DROP COLUMN travel_note;
        """
    )
