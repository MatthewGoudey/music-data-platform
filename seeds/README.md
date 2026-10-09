# Seeds

Reference data committed to the repo and loaded by jobs. Nothing here is personal listening data.
The queue spec (`docs/QUEUE_SPEC.md`, section 3) describes every file and how it loads.

Present:
- `lists/_lists.csv` — the list registry: slug, name, goal, weight, ranked, default_priority, file, source.
- `lists/rolling_stone_500.csv`, `lists/1001_albums.csv`, `lists/aoty_2007_2024.csv`,
  `lists/acclaimed_music_3000.csv` — published lists in the shared shape
  `position, artist, album, year, priority, genre, descriptors, note`. These are other people's
  compiled work: keep them out of the public repo (QUEUE_SPEC.md section 0, step 4).
- `atlas/` — the V Album Atlas (Matt's map of the home genre): `albums.csv` (2,942 albums, the
  `v_atlas` list), `album_notes.csv` (prose and sources per album), `lanes.csv`, `paths.csv`,
  `scenes.csv`, `labels.csv`, `tags.csv`, `artists.csv`.

To add:
- `lists/claude_canon.csv` — the Claude-curated canon exported from the old database (Phase 4, Block A).
- `venues.csv` — the 268 Chicago venues with travel times from the old database (Phase 3).
- `manual_tracklists.csv` — hand-set track counts for albums MusicBrainz and Last.fm lack.
- `festivals/` — one CSV per festival lineup (Phase 3).
- BestEverAlbums chart CSVs, downloaded by Matt from his free account (QUEUE_SPEC.md section 3a).
- Language starter lists, one per language, built with Claude (after Phase 4).
