"""identity: artist, artist_alias, release_group, release_group_alias

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-08

Identity is assigned once, at ingest: by MusicBrainz ID when ListenBrainz mapped the
listen, by norm_key otherwise. norm_key is UNIQUE only among rows without an MBID
(ADR 0015): MusicBrainz legitimately holds two artists named Nirvana and several
self-titled Weezer albums, and those must stay distinct.
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE artist (
            artist_id   INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            mbid        UUID UNIQUE,
            name        TEXT        NOT NULL,
            norm_key    TEXT        NOT NULL CHECK (norm_key <> ''),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX artist_norm_key_unmapped_uq ON artist (norm_key) WHERE mbid IS NULL;
        CREATE INDEX artist_norm_key_idx ON artist (norm_key);

        CREATE TABLE artist_alias (
            raw_name    TEXT        NOT NULL,
            artist_id   INTEGER     NOT NULL REFERENCES artist (artist_id) ON DELETE CASCADE,
            source      TEXT        NOT NULL
                        CHECK (source IN ('listenbrainz', 'musicbrainz', 'manual', 'show')),
            first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (raw_name, artist_id)
        );
        CREATE INDEX artist_alias_artist_idx ON artist_alias (artist_id);

        CREATE TABLE release_group (
            release_group_id   INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            mbid               UUID UNIQUE,
            artist_id          INTEGER     NOT NULL REFERENCES artist (artist_id),
            title              TEXT        NOT NULL,
            norm_key           TEXT        NOT NULL CHECK (norm_key <> ''),
            primary_type       TEXT
                               CHECK (primary_type IN ('Album', 'EP', 'Single', 'Broadcast', 'Other')),
            secondary_types    TEXT[]      NOT NULL DEFAULT '{}',
            first_release_year SMALLINT,
            is_compilation     BOOLEAN     NOT NULL DEFAULT false,
            is_box_set         BOOLEAN     NOT NULL DEFAULT false,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX release_group_key_unmapped_uq
            ON release_group (artist_id, norm_key) WHERE mbid IS NULL;
        CREATE INDEX release_group_artist_idx ON release_group (artist_id, norm_key);

        CREATE TABLE release_group_alias (
            artist_id         INTEGER     NOT NULL REFERENCES artist (artist_id) ON DELETE CASCADE,
            raw_album         TEXT        NOT NULL,
            release_group_id  INTEGER     NOT NULL
                              REFERENCES release_group (release_group_id) ON DELETE CASCADE,
            source            TEXT        NOT NULL
                              CHECK (source IN ('listenbrainz', 'musicbrainz', 'manual')),
            first_seen        TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (artist_id, raw_album, release_group_id)
        );
        CREATE INDEX release_group_alias_rg_idx ON release_group_alias (release_group_id);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS release_group_alias;
        DROP TABLE IF EXISTS release_group;
        DROP TABLE IF EXISTS artist_alias;
        DROP TABLE IF EXISTS artist;
        """
    )
