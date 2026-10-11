"""the Tastebreaker card replaces the wildcard

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-10

Queue spec v20 (Matt: "give me a tastebreaker card (similar to the wild card) - should be from a
genre I havent listned to recentely and should use claude canon as a tier 1 resource"; then "We
could just convert that [the wildcard] into the tastebreaker"). Every profile that drew a wildcard
draws a Tastebreaker instead.
"""

from __future__ import annotations

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """UPDATE queue_profile
              SET composition = composition || '{"tastebreaker": 1, "wildcard": 0}'::jsonb
            WHERE coalesce((composition ->> 'wildcard')::int, 0) > 0"""
    )


def downgrade() -> None:
    op.execute(
        """UPDATE queue_profile
              SET composition = (composition - 'tastebreaker') || '{"wildcard": 1}'::jsonb
            WHERE composition ? 'tastebreaker'"""
    )
