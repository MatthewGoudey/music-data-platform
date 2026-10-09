"""lists: list, list_entry, atlas_lane, atlas_lane_parent, atlas_path

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-09

docs/QUEUE_SPEC.md section 4. Every album on a list is a list entry, and every entry is
a queue candidate once it resolves to a release group. Keys are the identity layer's
(`norm_key`, `album_key`), so an entry and a listen of the same album meet on one key.
`list.file_rows` records how many distinct rows the seed file had when it was loaded,
so check L1 can run where the third-party files are absent (they stay off GitHub).
"""

from __future__ import annotations

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE list (
            list_id           INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            slug              TEXT        NOT NULL UNIQUE,
            name              TEXT        NOT NULL,
            goal              TEXT        NOT NULL
                              CHECK (goal IN ('canon', 'depth', 'breadth', 'personal')),
            weight            NUMERIC     NOT NULL DEFAULT 1.0,
            ranked            BOOLEAN     NOT NULL DEFAULT false,
            default_priority  TEXT
                              CHECK (default_priority IN ('Essential', 'Recommended', 'Deep cut')),
            source            TEXT,
            file_rows         INTEGER,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE atlas_lane (
            lane_id     TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            zone        TEXT,
            era_span    TEXT,
            definition  TEXT
        );

        CREATE TABLE atlas_lane_parent (
            lane_id         TEXT NOT NULL REFERENCES atlas_lane (lane_id) ON DELETE CASCADE,
            parent_lane_id  TEXT NOT NULL REFERENCES atlas_lane (lane_id) ON DELETE CASCADE,
            PRIMARY KEY (lane_id, parent_lane_id)
        );

        CREATE TABLE list_entry (
            entry_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            list_id           INTEGER     NOT NULL REFERENCES list (list_id) ON DELETE CASCADE,
            position          INTEGER,
            raw_artist        TEXT        NOT NULL,
            raw_album         TEXT        NOT NULL,
            year              INTEGER,
            priority          TEXT CHECK (priority IN ('Essential', 'Recommended', 'Deep cut')),
            lane_id           TEXT REFERENCES atlas_lane (lane_id),
            zone              TEXT,
            layer             TEXT,
            start_here        BOOLEAN     NOT NULL DEFAULT false,
            facets            JSONB       NOT NULL DEFAULT '{}'::jsonb,
            note              TEXT,
            artist_key        TEXT        NOT NULL CHECK (artist_key <> ''),
            album_key         TEXT        NOT NULL CHECK (album_key <> ''),
            release_group_id  INTEGER REFERENCES release_group (release_group_id) ON DELETE SET NULL,
            resolve_status    TEXT        NOT NULL DEFAULT 'pending'
                              CHECK (resolve_status IN ('pending', 'resolved', 'ambiguous', 'unresolved')),
            resolve_detail    JSONB,
            added_by          TEXT        NOT NULL DEFAULT 'seed',
            added_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            review_status     TEXT        NOT NULL DEFAULT 'accepted'
                              CHECK (review_status IN ('accepted', 'proposed', 'rejected')),
            UNIQUE (list_id, artist_key, album_key)
        );
        CREATE INDEX list_entry_release_group_idx ON list_entry (release_group_id);
        CREATE INDEX list_entry_pending_idx ON list_entry (resolve_status)
            WHERE resolve_status = 'pending';

        CREATE TABLE atlas_path (
            path                TEXT    NOT NULL,
            step                INTEGER NOT NULL,
            atlas_id            TEXT    NOT NULL,
            connection_to_next  TEXT,
            PRIMARY KEY (path, step)
        );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS atlas_path;
        DROP TABLE IF EXISTS list_entry;
        DROP TABLE IF EXISTS atlas_lane_parent;
        DROP TABLE IF EXISTS atlas_lane;
        DROP TABLE IF EXISTS list;
        """
    )
