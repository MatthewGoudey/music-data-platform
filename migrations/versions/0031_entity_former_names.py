"""entity.attrs.names: names an entity held before an import renamed it, recovered from evidence

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-22

Graph spec v13. `graph import` renamed an MBID entity to the latest credited name and dropped the
old one, so Wikipedia personnel claims accepted under it failed G2 ("Carter Family" after the
entity became "The Carter Family"). The importer now keeps the old name; this recovers the names
already lost from the accepted personnel evidence ("Wikipedia personnel: NAME — roles").
"""

from __future__ import annotations

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        WITH lost AS (
            SELECT a.subject_id AS entity_id,
                   jsonb_agg(DISTINCT substring(a.evidence FROM '^Wikipedia personnel: (.+?) — '))
                       AS names
              FROM assertion a JOIN entity e ON e.entity_id = a.subject_id
             WHERE a.extractor = 'firecrawl_json' AND a.predicate = 'credited_on'
               AND a.status = 'accepted' AND a.evidence LIKE 'Wikipedia personnel: % — %'
               AND substring(a.evidence FROM '^Wikipedia personnel: (.+?) — ') <> e.name
               AND NOT COALESCE(e.attrs -> 'names', '[]'::jsonb)
                       ? substring(a.evidence FROM '^Wikipedia personnel: (.+?) — ')
             GROUP BY a.subject_id
        )
        UPDATE entity e
           SET attrs = jsonb_set(e.attrs, '{names}',
                                 COALESCE(e.attrs -> 'names', '[]'::jsonb) || lost.names)
          FROM lost
         WHERE e.entity_id = lost.entity_id;
        """
    )


def downgrade() -> None:
    op.execute("UPDATE entity SET attrs = attrs - 'names' WHERE attrs ? 'names'")
