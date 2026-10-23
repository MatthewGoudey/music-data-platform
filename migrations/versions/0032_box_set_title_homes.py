"""Box-set listens a title match sent to another recording's album go back to the box set

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-22

Queue spec v25: a box set's listen goes to its home album by recording MBID only. Until now a
title match sent it too, so "One Too Many Mornings" from The Basement Tapes Complete (2026-10-18)
landed on The Times They Are A-Changin', a different recording. Derive never moves such a listen
back, so this moves the ones the old rule placed: on an album that lacks its recording, played
under the box set's name (the reported key is the box set's, extends it either way, or has its
words in another order) and not the album's, the recording on exactly one box set by the album's
artist. Prod on 2026-10-22: 37 listens, 7 recordings. Both groups are marked changed, so the
next derive rebuilds their sessions. The downgrade leaves the listens where they are.
"""

from __future__ import annotations

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION pg_temp.related(a TEXT, b TEXT) RETURNS BOOLEAN LANGUAGE sql IMMUTABLE AS $$
            SELECT a = b OR b LIKE a || ' %' OR a LIKE b || ' %'
                OR (SELECT array_agg(w ORDER BY w) FROM unnest(string_to_array(a, ' ')) w)
                 = (SELECT array_agg(w ORDER BY w) FROM unnest(string_to_array(b, ' ')) w)
        $$;

        WITH cand AS (
            SELECT l.listen_id, l.release_group_id AS from_rg, b.release_group_id AS box
              FROM listen l
              JOIN release_group g ON g.release_group_id = l.release_group_id
                                   AND NOT g.is_compilation AND NOT g.is_box_set
              JOIN release_group_track bt ON bt.recording_mbid = l.recording_mbid
              JOIN release_group b ON b.release_group_id = bt.release_group_id AND b.is_box_set
                                   AND b.artist_id = g.artist_id
             WHERE l.recording_mbid IS NOT NULL AND l.reported_key IS NOT NULL
               AND l.placed_by IS NULL
               AND NOT EXISTS (SELECT 1 FROM release_group_track t
                                WHERE t.release_group_id = g.release_group_id
                                  AND t.recording_mbid = l.recording_mbid)
               AND NOT pg_temp.related(l.reported_key, g.norm_key)
               AND pg_temp.related(l.reported_key, b.norm_key)
        ), one AS (
            SELECT listen_id, min(from_rg) AS from_rg, min(box) AS box
              FROM cand GROUP BY listen_id HAVING count(DISTINCT box) = 1
        ), moved AS (
            UPDATE listen l SET release_group_id = o.box
              FROM one o WHERE l.listen_id = o.listen_id
            RETURNING o.from_rg, o.box
        )
        UPDATE release_group SET updated_at = now()
         WHERE release_group_id IN (SELECT from_rg FROM moved UNION SELECT box FROM moved);

        DROP FUNCTION pg_temp.related(TEXT, TEXT);
        """
    )


def downgrade() -> None:
    pass  # a data repair: the old placement was the error
