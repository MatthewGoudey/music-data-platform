"""graph core: predicate, entity, assertion, entity_link, map_membership, graph_album, edge view

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-09

docs/graph/GRAPH_SPEC.md section 4 (ADR 0017). The graph stores claims, not facts: one
`assertion` row per subject–predicate–object with its source, basis, evidence, confidence
and status. The `edge` view gives the current view of each edge by the precedence in
docs/graph/edge-vocabulary.md section 3. Empty tables change nothing where no graph job runs.
"""

from __future__ import annotations

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE predicate (
            name           TEXT PRIMARY KEY,
            facet          TEXT    NOT NULL,
            subject_types  TEXT[]  NOT NULL,
            object_types   TEXT[]  NOT NULL,   -- '{literal}' for literal objects
            "symmetric"    BOOLEAN NOT NULL DEFAULT false,  -- quoted: a reserved word
            lineage        BOOLEAN NOT NULL DEFAULT false,  -- influenced_by, sounds_like, covers, samples
            description    TEXT    NOT NULL
        );

        CREATE TABLE entity (
            entity_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            -- an MBID artist is 'person' when MusicBrainz types it Person, else 'artist'; claim
            -- files' types are hints, and the structure check treats person as a kind of artist
            type              TEXT NOT NULL CHECK (type IN ('album','recording','work','artist','person',
                                  'label','place','area','scene','lane','map','path','list','tag','genre')),
            name              TEXT NOT NULL,
            norm_key          TEXT NOT NULL CHECK (norm_key <> ''),
            context_key       TEXT NOT NULL DEFAULT '',  -- artist norm_key for albums, recordings and works
            mbid              UUID,
            release_group_id  INTEGER REFERENCES release_group (release_group_id) ON DELETE SET NULL,
            artist_id         INTEGER REFERENCES artist (artist_id) ON DELETE SET NULL,
            local_key         TEXT,                       -- lane id, atlas id, list slug, tag name
            attrs             JSONB NOT NULL DEFAULT '{}'::jsonb,  -- year, disambiguation, artist, album, candidates
            resolve_status    TEXT NOT NULL DEFAULT 'resolved'
                              CHECK (resolve_status IN ('resolved','ambiguous','unresolved','local')),
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX entity_mbid_uq ON entity (type, mbid)
            WHERE mbid IS NOT NULL AND type NOT IN ('artist','person');
        CREATE UNIQUE INDEX entity_artist_mbid_uq ON entity (mbid)   -- one entity per MusicBrainz artist
            WHERE mbid IS NOT NULL AND type IN ('artist','person');
        CREATE UNIQUE INDEX entity_rg_uq ON entity (release_group_id) WHERE release_group_id IS NOT NULL;
        CREATE UNIQUE INDEX entity_local_uq ON entity (type, local_key) WHERE local_key IS NOT NULL;
        CREATE UNIQUE INDEX entity_unmapped_uq ON entity (type, norm_key, context_key)
            WHERE mbid IS NULL AND local_key IS NULL;
        CREATE INDEX entity_name_trgm ON entity USING gin (name gin_trgm_ops);

        CREATE TABLE assertion (
            assertion_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            claim_key       TEXT NOT NULL UNIQUE,   -- sha1 of subject, predicate, object, source, source_url, evidence
            claim_label     TEXT NOT NULL UNIQUE,   -- e.g. T1-A2184-L027
            replaces        BIGINT REFERENCES assertion (assertion_id),  -- a corrected re-extraction
            subject_id      BIGINT NOT NULL REFERENCES entity (entity_id) ON DELETE CASCADE,
            predicate       TEXT   NOT NULL REFERENCES predicate (name),
            object_id       BIGINT REFERENCES entity (entity_id) ON DELETE CASCADE,
            object_value    TEXT,
            qualifiers      JSONB  NOT NULL DEFAULT '{}'::jsonb,
            source          TEXT   NOT NULL,
            extractor       TEXT   NOT NULL CHECK (extractor IN
                                ('musicbrainz','discogs','wikidata','firecrawl_json','claude','matt')),
            basis           TEXT   NOT NULL CHECK (basis IN ('documented','reported','inferred')),
            direction       TEXT   CHECK (direction IN ('subject_newer')),
            evidence        TEXT   NOT NULL,        -- text over 300 characters fails the Evidence check
            source_url      TEXT,
            fetch_id        BIGINT,                 -- FK added in 0019
            album_context   BIGINT REFERENCES entity (entity_id),  -- the album being researched
            batch_id        INTEGER,                -- FK added in 0019
            status          TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN
                                ('proposed','unread','accepted','rejected','ask_matt','superseded')),
            confidence      NUMERIC(3,2) NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 1),
            checks          JSONB  NOT NULL DEFAULT '{}'::jsonb,
            fails           TEXT[] NOT NULL DEFAULT '{}',
            support         TEXT[] NOT NULL DEFAULT '{}',   -- independent sources that agree
            reader_verdict  TEXT,
            reader_reason   TEXT,
            reader_first    TEXT,                   -- the reader's first verdict, kept for the M2 measure
            asserted_by     TEXT   NOT NULL,
            asserted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            pipeline_run_id BIGINT,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK ((object_id IS NULL) <> (object_value IS NULL))
        );
        CREATE INDEX assertion_subject_idx ON assertion (subject_id, predicate);
        CREATE INDEX assertion_object_idx ON assertion (object_id, predicate);
        CREATE INDEX assertion_album_idx ON assertion (album_context);
        CREATE INDEX assertion_open_idx ON assertion (status)
            WHERE status IN ('proposed','unread','ask_matt');

        CREATE TABLE entity_link (
            entity_id    BIGINT NOT NULL REFERENCES entity (entity_id) ON DELETE CASCADE,
            kind         TEXT   NOT NULL CHECK (kind IN ('wikipedia','wikidata','musicbrainz','discogs',
                             'allmusic','bandcamp','official','label_page','interview','review',
                             'live_session','streaming','liner_notes','other')),
            url          TEXT   NOT NULL,
            language     TEXT,
            source       TEXT   NOT NULL,
            verified_at  TIMESTAMPTZ,
            status       TEXT   NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','dead','blocked')),
            PRIMARY KEY (entity_id, url)
        );

        CREATE TABLE map_membership (
            entity_id  BIGINT NOT NULL REFERENCES entity (entity_id) ON DELETE CASCADE,
            map        TEXT   NOT NULL,        -- 'v_atlas'
            coords     JSONB  NOT NULL,        -- atlas_id, lane, zone, layer, priority, start_here
            PRIMARY KEY (entity_id, map)
        );

        CREATE TABLE graph_album (                -- research progress per target album
            entity_id      BIGINT PRIMARY KEY REFERENCES entity (entity_id) ON DELETE CASCADE,
            slices         TEXT[] NOT NULL DEFAULT '{}',
            priority       TEXT,                  -- Essential, Recommended, Deep cut (from the atlas)
            baseline_at    TIMESTAMPTZ,
            fetched_at     TIMESTAMPTZ,
            facts_at       TIMESTAMPTZ,
            batch_id       INTEGER,
            verified_at    TIMESTAMPTZ,
            notes          JSONB NOT NULL DEFAULT '{}'::jsonb   -- gaps, no-match reasons
        );

        CREATE VIEW edge AS
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
        GROUP BY subject_id, predicate, object_id, object_value;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP VIEW IF EXISTS edge;
        DROP TABLE IF EXISTS graph_album, map_membership, entity_link, assertion, entity, predicate;
        """
    )
