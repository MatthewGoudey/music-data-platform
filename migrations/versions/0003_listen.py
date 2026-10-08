"""listen: one row per ListenBrainz listen

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08

artist_id is NOT NULL (closes audit B2). The natural key (listened_at, artist_id,
norm_title) makes re-ingesting any window idempotent. Raw strings stay for display
and audit; they are never join keys. No raw_payload: ListenBrainz keeps the source.
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE listen (
            listen_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            listened_at       TIMESTAMPTZ NOT NULL,
            artist_id         INTEGER     NOT NULL REFERENCES artist (artist_id),
            release_group_id  INTEGER     REFERENCES release_group (release_group_id),
            track_name        TEXT        NOT NULL,
            norm_title        TEXT        NOT NULL,
            artist_name       TEXT        NOT NULL,
            release_name      TEXT,
            recording_mbid    UUID,
            release_mbid      UUID,
            recording_msid    UUID,
            duration_ms       INTEGER,
            client            TEXT,
            inserted_at       TIMESTAMPTZ,
            loaded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (listened_at, artist_id, norm_title)
        );
        CREATE INDEX listen_listened_at_idx ON listen (listened_at DESC);
        CREATE INDEX listen_artist_idx ON listen (artist_id, listened_at);
        CREATE INDEX listen_release_group_idx ON listen (release_group_id, listened_at);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS listen;")
