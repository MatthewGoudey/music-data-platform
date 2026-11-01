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

A home is a mapped group, never an expanded form of the current album ("Play & Play: The
B Sides" on Play), never a compilation or box set for a listen on a real album, and among
namesakes the one already holding listens reported under that name. Applied to dev
(rekey, resolve's unmapped tail, full derive): 33 albums gained full sessions (At Folsom
Prison 12, Chet Baker Sings 9, Sgt. Pepper 6, Endtroducing 3 …), full sessions rose from
3,106 to 3,214, heard list entries from 1,625 to 1,689, every listening check stayed
green, and one album lost its only session (Etta James, At Last!: 8 of 10 became 7 of 10
when a track its player reported under another album stopped being pulled in).

A group whose tracklist cannot be resolved holds no session, so derive also folds its listens
into the one mapped namesake (same album key, same artist or the credit's lead artist) whose
tracklist is resolved or not yet fetched (`derive/fold.py`). In prod this covered 15 albums and
203 listens, RAM among them (98 listens, then 5 full sessions in dev).

## Amendment (2026-10-17): whole sittings, and compilations at 80%

Matt played *The Velvet Underground & Nico* front to back and ListenBrainz filed the tracks under
four compilations. The player reported "The Velvet Underground & Nico 45th Anniversary", which the
edition rule did not strip (it now cuts a bare trailing "<n>th Anniversary [Edition]", plus Spanish
and Portuguese edition words and K-pop's " - The 2nd Album"). A sweep of prod found 219 of 6,561
album runs still scattered, for reasons no per-listen rule can see. Three rules close them:

- **Runs.** A run is consecutive listens under one reported album, each within 30 minutes. Its
  candidates are mapped groups with the reported key, or by one of its artists with a key that nearly
  matches or extends it ("Cuttin' Grass - Vol. 2 (Cowboy Arms Sessions)" for "Cuttin' Grass Vol. 2:
  The Cowboy Arms Sessions"). The one group whose tracklist places the most of its titles (at least
  80%; ties to the reported key, then recording matches, then listens held) takes the matched
  listens, marked `listen.placed_by = 'run'` (migration 0030); the reported-album rule and the
  stray-track redirect skip them. Twins split by recordings (Twin Fantasy), MusicBrainz duplicates by
  coverage (Minewest Emo).
- **Compilations at 80% (Matt).** A compilation or box set wins a run only when the run covers 80% of
  its tracklist: that is listening to the compilation, and it holds the session (sessions no longer
  exclude compilations). A listen on a compilation outside such a run goes to its track's home album:
  the one album or EP by the listen's or the compilation's artist holding the recording (by MBID, else
  by title among groups with no secondary types; several go to the earliest). A rarity stays. This
  replaces "compilation plays stay on the compilation" above, and Cap'n Jazz EPs played from the
  anthology under 80% still go to their EPs.
- **Evidence for unmapped groups.** The unmapped tail counts the artist's listens reported under a
  group's name but filed elsewhere, and their titles: Nilsson Schmilsson held 2 of its 23 plays and
  was never searched. Lists resolve to a known group only when it is mapped.

Simulated on prod before release: 7,225 of 8,149 runs placed, 1,153 listens moved (Their Greatest
Hits 1971–1975 266, Harbor 218, Cuttin' Grass 51), and 1,761 compilation listens sent to their home
albums. `tests/integration/fixtures/listen_homes.json` holds one scenario per failure family; the
dq check `album_runs_scattered_pct` (≤ 2% of the last 30 days' runs) watches for new ones without
reading album names, and `musicdata identity impact` lists what a key rule change would move.

Addendum (2026-10-22, queue spec v25): a box set's listen goes home by recording MBID only; the
title fallback stays for compilations. Run candidates also match a key without a leading "a"/"an"
or with its words in another order, and `loose_title_keys` drops a bracketed "(with …)" credit.
The fixture gains one scenario for each (trio with credit, tribute without its article, box set
takes); migration 0032 repairs the 37 listens the box-set title rule had moved.

## Amendment (2026-11-01): a comma-joined name goes to its known first artist

Pano Scrobbler sends a multi-artist track as one comma-joined `artist_name` with no artist list
and no album artist ("Wednesday, Advance Base", "THAO, Tune-Yards, The Linda Lindas"), so
`primary_artist` kept the whole string and ingest made an artist of it: 581 such rows held 1,722
listens on 2026-11-01. Ingest now gives an unmapped "A, B, …" to A when A's key names exactly one
mapped artist and the whole name names none (`IdentityIndex._known_head`). A mapped name keeps
its commas ("Tyler, The Creator"), and a first name with no MBID changes nothing ("Malibu Ken,
Aesop Rock" stays). Stored comma rows are unmapped, so `ingest --rekey` moves their listens:
275 rows, about 1,150 listens, in prod.

Addendum (2026-11-01): sessions match a title the album has twice to its first track, as an exact
title already did ("San Quentin" on *At San Quentin*); a loose key that different titles share
still names neither. `loose_title_keys` also drops a leading bracketed phrase ("(There'll Be)
Peace in the Valley" → "peace in the valley"). Replayed on prod's 16,155 albums with
tracklists: 4 albums changed, none lost a track (Otis Blue 10 → 11 of 11; At San Quentin 5 → 7
of 9 before its run placement).
