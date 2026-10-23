"""Index source_fetch.entity_id: deleting a merged entity checks it

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-23

`graph verify --pending` merges each duplicate entity into the one with an MBID and deletes it
(graph spec v10). The delete checks every foreign key to `entity`, and `source_fetch.entity_id`
alone had no index, so each merge scanned the table: about a second each. A backlog of 16,212
merges after five failed nights ran the nightly graph job into its 4-hour limit twice
(2026-10-23).
"""

from __future__ import annotations

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX source_fetch_entity_idx ON source_fetch (entity_id)")


def downgrade() -> None:
    op.execute("DROP INDEX source_fetch_entity_idx")
