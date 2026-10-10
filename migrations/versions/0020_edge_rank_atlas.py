"""edge view: the atlas ranks as one ordinary source, below databases and documented pages

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-09

docs/graph/GRAPH_SPEC.md change v4 (Matt): the atlas is AI-written, so where sources disagree
the current view of an edge follows Matt, then the databases, then documented or reported
pages, then the atlas's own statements, then anything inferred (a map's or an extractor's
alike), by confidence within a rank.
"""

from __future__ import annotations

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

VIEW = """
CREATE OR REPLACE VIEW edge AS
SELECT subject_id, predicate, object_id, object_value,
       max(confidence)                         AS confidence,
       array_agg(DISTINCT source)              AS sources,
       (array_agg(assertion_id ORDER BY
           CASE WHEN extractor = 'matt'                                   THEN 1
                WHEN extractor IN ('musicbrainz','discogs','wikidata')    THEN 2
                WHEN basis IN ('documented','reported')
                     AND source NOT LIKE 'map:%'                          THEN 3
                WHEN basis IN ('documented','reported')                   THEN 4
                ELSE 5 END,
           confidence DESC))[1]                AS best_assertion_id
FROM assertion WHERE status = 'accepted'
GROUP BY subject_id, predicate, object_id, object_value
"""

OLD_VIEW = """
CREATE OR REPLACE VIEW edge AS
SELECT subject_id, predicate, object_id, object_value,
       max(confidence)                         AS confidence,
       array_agg(DISTINCT source)              AS sources,
       (array_agg(assertion_id ORDER BY
           CASE WHEN extractor = 'matt'                                   THEN 1
                WHEN source LIKE 'map:%' AND basis <> 'inferred'          THEN 2
                WHEN extractor IN ('musicbrainz','discogs','wikidata')    THEN 3
                WHEN basis IN ('documented','reported')                   THEN 4
                WHEN source LIKE 'map:%'                                  THEN 5
                ELSE 6 END,
           confidence DESC))[1]                AS best_assertion_id
FROM assertion WHERE status = 'accepted'
GROUP BY subject_id, predicate, object_id, object_value
"""


def upgrade() -> None:
    op.execute(VIEW)


def downgrade() -> None:
    op.execute(OLD_VIEW)
