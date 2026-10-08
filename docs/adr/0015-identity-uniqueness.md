# ADR 0015: Identity uniqueness and the listen's artist

- Status: Accepted
- Date: 2026-10-08
- Decision: `norm_key` is unique only among rows without an MBID; a listen's `artist_id` is its first credited artist

## Uniqueness

The plan puts a plain UNIQUE on `artist.norm_key` and on `release_group (artist_id, norm_key)`.
MusicBrainz holds distinct entities that share a name: two artists called Nirvana, and
several self-titled Weezer albums (Blue, Green, Red, White). A plain UNIQUE would merge them.

So the UNIQUE applies where `mbid IS NULL` (partial unique indexes in migration 0002), and
`mbid` is UNIQUE on its own. That still closes B3, A3 and A4: those duplicates were spelling
variants of one entity, and spelling variants now resolve to one row either through the
MBID or through the fallback key.

Ingest rules that keep this tight:

- A mapped listen finds its artist by MBID. If none exists but exactly one unmapped artist
  has the same `norm_key`, that row receives the MBID instead of a new row being created.
- An unmapped listen finds its artist by `norm_key`: the unmapped row if one exists,
  otherwise the mapped artist when exactly one has that key, otherwise a new unmapped row.
- The same two rules apply to release groups, scoped to the artist.

The acceptance check for duplicates counts unmapped rows sharing a key (always 0, by the
index) plus mapped and unmapped rows sharing a key, which go to review.

## The listen's artist

ListenBrainz credits a track to a list of artists ("Kendrick Lamar feat. SZA" is two).
`listen.artist_id` is the first credited artist, which is the primary artist in MusicBrainz
credit order; for an unmapped listen it is the name before any "feat." marker. Featured
artists stay visible in the raw `artist_name`. A `listen_artist` table for per-credit stats
is deferred until a question needs it.
