"""shows: venue, show, show_source, show_artist, show_interest

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-08

One show per (venue, local date, headliner key) whichever site listed it; each listing
is a show_source row, so Oh My Rockness and Ticketmaster can describe the same night.
A venue is matched by the source's own id, then by name key, then by location. Show
artists keep the raw listing beside the clean name, and artist_id stays NULL when the
performer is not an artist in the listening history (closes audit E1–E3 by construction
plus the acceptance checks).
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE venue (
            venue_id    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            name        TEXT        NOT NULL,
            norm_key    TEXT        NOT NULL UNIQUE CHECK (norm_key <> ''),
            address     TEXT,
            latitude    DOUBLE PRECISION,
            longitude   DOUBLE PRECISION,
            website     TEXT,
            omr_slug          TEXT UNIQUE,
            ticketmaster_id   TEXT UNIQUE,
            -- Crawl state: venues with upcoming shows are read nightly, the rest weekly.
            omr_upcoming    INTEGER,
            omr_checked_at  TIMESTAMPTZ,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE show (
            show_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            venue_id       INTEGER     NOT NULL REFERENCES venue (venue_id),
            starts_at      TIMESTAMPTZ NOT NULL,
            show_date      DATE        NOT NULL,  -- the Chicago calendar date
            headliner_key  TEXT        NOT NULL,
            title          TEXT        NOT NULL,
            non_artist     BOOLEAN     NOT NULL DEFAULT false,
            cancelled      BOOLEAN     NOT NULL DEFAULT false,
            missed_runs    INTEGER     NOT NULL DEFAULT 0,
            first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (venue_id, show_date, headliner_key)
        );
        CREATE INDEX show_starts_at_idx ON show (starts_at);

        CREATE TABLE show_source (
            source       TEXT        NOT NULL CHECK (source IN ('omr', 'do312', 'ticketmaster')),
            source_id    TEXT        NOT NULL,
            show_id      BIGINT      NOT NULL REFERENCES show (show_id) ON DELETE CASCADE,
            url          TEXT        NOT NULL,
            tickets_url  TEXT,
            on_sale_at   TIMESTAMPTZ,
            price        TEXT,
            seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (source, source_id)
        );
        CREATE INDEX show_source_show_idx ON show_source (show_id);

        CREATE TABLE show_artist (
            show_id      BIGINT  NOT NULL REFERENCES show (show_id) ON DELETE CASCADE,
            position     INTEGER NOT NULL CHECK (position > 0),
            role         TEXT    NOT NULL CHECK (role IN ('headliner', 'support')),
            raw_name     TEXT    NOT NULL,
            clean_name   TEXT    NOT NULL,
            note         TEXT,
            artist_id    INTEGER REFERENCES artist (artist_id) ON DELETE SET NULL,
            source_slug  TEXT,
            PRIMARY KEY (show_id, position)
        );
        CREATE INDEX show_artist_artist_idx ON show_artist (artist_id);

        CREATE TABLE show_interest (
            show_id     BIGINT      PRIMARY KEY REFERENCES show (show_id) ON DELETE CASCADE,
            status      TEXT        NOT NULL CHECK (status IN ('interested', 'going')),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS show_interest;
        DROP TABLE IF EXISTS show_artist;
        DROP TABLE IF EXISTS show_source;
        DROP TABLE IF EXISTS show;
        DROP TABLE IF EXISTS venue;
        """
    )
