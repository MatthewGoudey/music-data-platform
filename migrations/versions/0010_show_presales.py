"""show_source.presales: every presale window a listing names

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-09

Ticketmaster lists several presales per event (artist, venue, card member, Spotify),
each with its own window. All are kept as [{"name", "start", "end"}]; the API picks the
next one at request time, so parsing never needs "now".
"""

from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE show_source ADD COLUMN presales JSONB NOT NULL DEFAULT '[]'::jsonb;")


def downgrade() -> None:
    op.execute("ALTER TABLE show_source DROP COLUMN IF EXISTS presales;")
