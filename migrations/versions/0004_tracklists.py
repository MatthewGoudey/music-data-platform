"""tracklists: release_group_tracklist and release_group_track

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08

One tracklist per release group: the canonical release's tracks, which is what
completion is measured against. `source = 'unresolved'` records a failed lookup so the
resolver does not retry it every night; `resolved_at` says when to try again.
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE release_group_tracklist (
            release_group_id  INTEGER     PRIMARY KEY
                              REFERENCES release_group (release_group_id) ON DELETE CASCADE,
            track_count       INTEGER     CHECK (track_count > 0),
            release_mbid      UUID,
            source            TEXT        NOT NULL
                              CHECK (source IN ('musicbrainz', 'lastfm', 'manual', 'unresolved')),
            resolved_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            note              TEXT,
            CHECK ((source = 'unresolved') = (track_count IS NULL))
        );

        CREATE TABLE release_group_track (
            release_group_id  INTEGER  NOT NULL
                              REFERENCES release_group_tracklist (release_group_id) ON DELETE CASCADE,
            position          INTEGER  NOT NULL CHECK (position > 0),
            title             TEXT     NOT NULL,
            norm_title        TEXT     NOT NULL,
            recording_mbid    UUID,
            length_ms         INTEGER,
            PRIMARY KEY (release_group_id, position)
        );
        CREATE INDEX release_group_track_recording_idx ON release_group_track (recording_mbid);
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TABLE IF EXISTS release_group_track; DROP TABLE IF EXISTS release_group_tracklist;"
    )
