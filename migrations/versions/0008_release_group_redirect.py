"""release_group_redirect: stray tracks ListenBrainz filed under the wrong album

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-08

When a recording sits in the middle of a listening run on album D but ListenBrainz
mapped it to group S, and D's standard tracklist has the recording while S cannot hold
a session for it, derive learns (recording, S) → D here and moves those listens. The
row keeps the correction across re-ingests, which bring back ListenBrainz's mapping.
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE release_group_redirect (
            recording_mbid         UUID        NOT NULL,
            from_release_group_id  INTEGER     NOT NULL
                                   REFERENCES release_group (release_group_id) ON DELETE CASCADE,
            to_release_group_id    INTEGER     NOT NULL
                                   REFERENCES release_group (release_group_id) ON DELETE CASCADE,
            evidence               INTEGER     NOT NULL,
            learned_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (recording_mbid, from_release_group_id),
            CHECK (from_release_group_id <> to_release_group_id)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS release_group_redirect;")
