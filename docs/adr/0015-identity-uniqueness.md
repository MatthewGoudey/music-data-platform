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

## Amendment (2026-10-08): one album, one group

Spot checks found albums split across groups ("islands"); about 200 listening runs
switched between two groups with the same title. Three rules close the main causes:

- **Album artist.** An unmapped album is keyed to the release's artist when the listen
  carries one (`release_artist_name`), not to each track's artist, so a soundtrack or a
  collaboration credited per track is one group. The listen itself keeps its track artist.
- **Local merge without a minimum.** An unmapped group folds into a mapped namesake
  (same artist and key) by tracklist overlap whatever its listen count; only MusicBrainz
  searches keep the three-listen minimum.
- **Stray tracks.** When ListenBrainz files one track of an album run under another group
  (the single, a compilation, a live bootleg), derive moves it to the album around it,
  if that album's standard tracklist has the recording and the other group cannot place
  it. Each correction is learned once into `release_group_redirect`, so re-ingests keep it.
  In dev this moved 2,157 listens (0.9%) through 110 corrections.

Re-recordings named for their artist ("Red (Taylor's Version)") are different albums,
so "<name>'s Version" stays in the album key. The acceptance checks report
`listening_runs_split_across_groups` (not gated) to track what remains.

## Amendment (2026-10-09): the album the player reported

Matt played *At Folsom Prison* front to back on sixteen days, yet it never had a session:
ListenBrainz maps each track on its own and filed that album's live recordings under
thirteen groups (Classic Cash, American IV, Silver …). In dev, 830 reported album names
pointed at groups other than their own. Two rules close it:

- **Reported album first.** Every listen stores `reported_key`, the album key of the
  release name its player reported (migration 0015). Derive moves a listen whose key
  differs from its group's key to that album's home: the one group with the reported key
  by the listen's artist, its current group's artist, or an album artist the name was
  seen under (exactly one, or exactly one mapped among several). A listen at home is
  exempt from the stray-track redirect, so the two rules never trade a listen back and
  forth. Compilation plays now stay on the compilation instead of crediting the albums
  their tracks came from.
- **Looser track matching, never stored.** When a played track's exact title key matches
  no track of the tracklist, sessions try `loose_title_keys`: numbered parts alike
  ("Pt. I" = "Part 1"), a trailing "live at …" description dropped, and either half of a
  " / " track. Stored keys do not change, so no rekey is needed.
