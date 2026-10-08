"""sessions and stats: album_session, release_group_stat, artist_stat

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-08

Scrobbled sessions are derived (rebuilt per release group by `musicdata derive`);
manual sessions (vinyl, a show) are entered through the API and never rebuilt. The
stat tables are materialized: `derive` replaces their contents after each ingest.
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE album_session (
            session_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            release_group_id  INTEGER       NOT NULL
                              REFERENCES release_group (release_group_id) ON DELETE CASCADE,
            started_at        TIMESTAMPTZ   NOT NULL,
            ended_at          TIMESTAMPTZ   NOT NULL CHECK (ended_at >= started_at),
            tracks_played     INTEGER       NOT NULL CHECK (tracks_played >= 0),
            track_count       INTEGER       NOT NULL CHECK (track_count > 0),
            completion        NUMERIC(4, 3) NOT NULL CHECK (completion BETWEEN 0 AND 1),
            session_type      TEXT          NOT NULL CHECK (session_type IN ('full', 'partial')),
            source            TEXT          NOT NULL CHECK (source IN ('scrobbled', 'manual')),
            listen_count      INTEGER       NOT NULL DEFAULT 0,
            note              TEXT,
            created_at        TIMESTAMPTZ   NOT NULL DEFAULT now(),
            UNIQUE (release_group_id, started_at, source)
        );
        CREATE INDEX album_session_started_idx ON album_session (started_at DESC);

        CREATE TABLE release_group_stat (
            release_group_id    INTEGER PRIMARY KEY
                                REFERENCES release_group (release_group_id) ON DELETE CASCADE,
            listens             INTEGER       NOT NULL,
            distinct_tracks     INTEGER       NOT NULL,
            tracks_heard        INTEGER,
            track_count         INTEGER,
            best_completion     NUMERIC(4, 3),
            full_sessions       INTEGER       NOT NULL,
            partial_sessions    INTEGER       NOT NULL,
            first_listened_at   TIMESTAMPTZ   NOT NULL,
            last_listened_at    TIMESTAMPTZ   NOT NULL,
            last_full_session_at TIMESTAMPTZ
        );

        CREATE TABLE artist_stat (
            artist_id           INTEGER PRIMARY KEY REFERENCES artist (artist_id) ON DELETE CASCADE,
            listens             INTEGER     NOT NULL,
            distinct_tracks     INTEGER     NOT NULL,
            release_groups      INTEGER     NOT NULL,
            full_sessions       INTEGER     NOT NULL,
            first_listened_at   TIMESTAMPTZ NOT NULL,
            last_listened_at    TIMESTAMPTZ NOT NULL
        );
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TABLE IF EXISTS artist_stat; DROP TABLE IF EXISTS release_group_stat; "
        "DROP TABLE IF EXISTS album_session;"
    )
