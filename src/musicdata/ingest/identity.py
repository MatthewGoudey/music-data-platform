"""Identity assignment at ingest: every listen gets an artist_id, and a release_group_id
when it names a release. The rules are ADR 0015's:

- mapped (has an MBID): find by MBID; otherwise promote the one unmapped row with the
  same key by giving it the MBID; otherwise create.
- unmapped: find the unmapped row with the key; otherwise the mapped row when exactly
  one has the key; otherwise create.

The index lives in memory for one ingest run and is written through to the database a
page at a time, so a page costs a handful of statements, not one per listen.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import asyncpg

from musicdata.ingest.parse import Credit, ParsedListen

UNTITLED = "untitled"


@dataclass
class _Keys[K]:
    """by_mbid, unmapped-by-key and mapped-by-key lookups for one entity type."""

    by_mbid: dict[str, int] = field(default_factory=dict)
    unmapped: dict[K, int] = field(default_factory=dict)
    mapped: dict[K, set[int]] = field(default_factory=lambda: defaultdict(set))

    def add(self, entity_id: int, mbid: str | None, key: K) -> None:
        if mbid:
            self.by_mbid[mbid] = entity_id
            self.mapped[key].add(entity_id)
        else:
            self.unmapped[key] = entity_id

    def promote(self, entity_id: int, mbid: str, key: K) -> None:
        self.unmapped.pop(key, None)
        self.add(entity_id, mbid, key)

    def find(self, mbid: str | None, key: K) -> int | None:
        if mbid:
            return self.by_mbid.get(mbid)
        if key in self.unmapped:
            return self.unmapped[key]
        candidates = self.mapped.get(key)
        if candidates and len(candidates) == 1:
            return next(iter(candidates))
        return None


@dataclass
class PageResult:
    listens_inserted: int = 0
    listens_rekeyed: int = 0
    artists_created: int = 0
    artists_promoted: int = 0
    release_groups_created: int = 0
    release_groups_promoted: int = 0


class IdentityIndex:
    def __init__(self) -> None:
        self.artists: _Keys[str] = _Keys()
        self.release_groups: _Keys[tuple[int, str]] = _Keys()

    @classmethod
    async def load(cls, conn: asyncpg.Connection) -> IdentityIndex:
        idx = cls()
        for r in await conn.fetch("SELECT artist_id, mbid::text, norm_key FROM artist"):
            idx.artists.add(r["artist_id"], r["mbid"], r["norm_key"])
        for r in await conn.fetch(
            "SELECT release_group_id, mbid::text, artist_id, norm_key FROM release_group"
        ):
            idx.release_groups.add(
                r["release_group_id"], r["mbid"], (r["artist_id"], r["norm_key"])
            )
        return idx

    async def write_page(
        self, conn: asyncpg.Connection, page: list[ParsedListen], *, rekey: bool = False
    ) -> PageResult:
        """Assign identity to every listen in the page and insert the listens. One transaction.

        With `rekey`, stored listens of the page whose identity rules now give another
        artist or release group are moved onto it first (after an identity rule fix).
        """
        result = PageResult()
        async with conn.transaction():
            artist_ids = await self._credits(conn, [p.artist for p in page], result)
            album_artist_ids = await self._credits(conn, [p.album_artist for p in page], result)
            rg_ids = await self._release_groups(conn, page, album_artist_ids, result)
            await self._aliases(conn, page, artist_ids, album_artist_ids, rg_ids)
            if rekey:
                result.listens_rekeyed = await self._rekey(conn, page, artist_ids, rg_ids)
            result.listens_inserted = await self._listens(conn, page, artist_ids, rg_ids)
        return result

    @staticmethod
    async def _rekey(
        conn: asyncpg.Connection,
        page: list[ParsedListen],
        artist_ids: list[int],
        rg_ids: list[int | None],
    ) -> int:
        """Give stored listens the identity today's rules derive: artist, group and title key.

        Stored listens are matched on what never changes (time, raw artist, raw title), so
        a change to any key rule is caught. Exactly one row survives per new natural key:
        a stored listen whose new key another row already holds is a duplicate and goes.
        """
        args = (
            [p.listened_at for p in page],
            [p.artist_name for p in page],
            [p.track_name for p in page],
            [p.norm_title for p in page],
            artist_ids,
            rg_ids,
        )
        targets = """
            WITH u AS (
                SELECT DISTINCT *
                  FROM unnest($1::timestamptz[], $2::text[], $3::text[], $4::text[],
                              $5::int[], $6::int[])
                       AS u(listened_at, artist_name, track_name, norm_title, artist_id,
                            release_group_id)
            )
            SELECT l.listen_id, l.listened_at, u.artist_id, u.release_group_id, u.norm_title,
                   row_number() OVER (PARTITION BY l.listened_at, u.artist_id, u.norm_title
                                      ORDER BY l.listen_id) AS rn
              FROM u JOIN listen l ON l.listened_at = u.listened_at
                                  AND l.artist_name = u.artist_name
                                  AND l.track_name = u.track_name
        """
        await conn.execute(
            f"""WITH t AS ({targets})
                DELETE FROM listen d USING t
                 WHERE d.listen_id = t.listen_id
                   AND (t.rn > 1
                        OR EXISTS (SELECT 1 FROM listen x
                                    WHERE x.listened_at = t.listened_at
                                      AND x.artist_id = t.artist_id
                                      AND x.norm_title = t.norm_title
                                      AND x.listen_id NOT IN (SELECT listen_id FROM t)))""",
            *args,
        )
        status = await conn.execute(
            f"""WITH t AS ({targets})
                UPDATE listen l
                   SET artist_id = t.artist_id, release_group_id = t.release_group_id,
                       norm_title = t.norm_title
                  FROM t
                 WHERE l.listen_id = t.listen_id AND t.rn = 1
                   AND (l.artist_id <> t.artist_id
                        OR l.release_group_id IS DISTINCT FROM t.release_group_id
                        OR l.norm_title <> t.norm_title)""",
            *args,
        )
        return int(status.split()[-1])

    async def _credits(
        self, conn: asyncpg.Connection, credits: list[Credit], result: PageResult
    ) -> list[int]:
        """An artist_id for every credit, creating or promoting artists as ADR 0015 says."""
        # Mapped credits first, so an unmapped spelling in the same page can land on them.
        missing_mapped: dict[str, tuple[str, str]] = {}
        for a in credits:
            if a.mbid and self.artists.find(a.mbid, a.key) is None:
                missing_mapped.setdefault(a.mbid, (a.name, a.key))

        promote: list[tuple[int, str, str]] = []
        create: list[tuple[str, str, str | None]] = []
        for mbid, (name, key) in missing_mapped.items():
            unmapped_id = self.artists.unmapped.get(key)
            if unmapped_id is not None:
                promote.append((unmapped_id, mbid, name))
                self.artists.promote(unmapped_id, mbid, key)
            else:
                create.append((name, key, mbid))
        if promote:
            await conn.execute(
                """UPDATE artist a SET mbid = u.mbid, name = u.name
                     FROM unnest($1::int[], $2::uuid[], $3::text[]) AS u(artist_id, mbid, name)
                    WHERE a.artist_id = u.artist_id AND a.mbid IS NULL""",
                [x[0] for x in promote],
                [x[1] for x in promote],
                [x[2] for x in promote],
            )
            result.artists_promoted += len(promote)

        if create:
            await self._insert_artists(conn, create, result)

        unmapped_new: dict[str, str] = {}
        for a in credits:
            if a.mbid is None and self.artists.find(None, a.key) is None:
                unmapped_new.setdefault(a.key, a.name)
        if unmapped_new:
            await self._insert_artists(
                conn, [(name, key, None) for key, name in unmapped_new.items()], result
            )

        ids: list[int] = []
        for a in credits:
            found = self.artists.find(a.mbid, a.key)
            if found is None:
                raise RuntimeError(f"artist not resolved after write: {a}")
            ids.append(found)
        return ids

    async def _insert_artists(
        self,
        conn: asyncpg.Connection,
        create: list[tuple[str, str, str | None]],
        result: PageResult,
    ) -> None:
        rows = await conn.fetch(
            """INSERT INTO artist (name, norm_key, mbid)
               SELECT * FROM unnest($1::text[], $2::text[], $3::uuid[])
               RETURNING artist_id, mbid::text, norm_key""",
            [c[0] for c in create],
            [c[1] for c in create],
            [c[2] for c in create],
        )
        for r in rows:
            self.artists.add(r["artist_id"], r["mbid"], r["norm_key"])
        result.artists_created += len(rows)

    async def _release_groups(
        self,
        conn: asyncpg.Connection,
        page: list[ParsedListen],
        artist_ids: list[int],
        result: PageResult,
    ) -> list[int | None]:
        def key_of(p: ParsedListen, artist_id: int) -> tuple[int, str]:
            return (artist_id, p.release_key or UNTITLED)

        missing_mapped: dict[str, tuple[int, str, str]] = {}
        for p, aid in zip(page, artist_ids, strict=True):
            mbid = p.release_group_mbid
            if mbid and self.release_groups.find(mbid, key_of(p, aid)) is None:
                missing_mapped.setdefault(
                    mbid, (aid, p.release_name or UNTITLED, key_of(p, aid)[1])
                )

        promote: list[tuple[int, str, str]] = []
        create: list[tuple[int, str, str, str | None]] = []
        for mbid, (aid, title, key) in missing_mapped.items():
            unmapped_id = self.release_groups.unmapped.get((aid, key))
            if unmapped_id is not None:
                promote.append((unmapped_id, mbid, title))
                self.release_groups.promote(unmapped_id, mbid, (aid, key))
            else:
                create.append((aid, title, key, mbid))
        if promote:
            await conn.execute(
                """UPDATE release_group r SET mbid = u.mbid, updated_at = now()
                     FROM unnest($1::int[], $2::uuid[]) AS u(release_group_id, mbid)
                    WHERE r.release_group_id = u.release_group_id AND r.mbid IS NULL""",
                [x[0] for x in promote],
                [x[1] for x in promote],
            )
            result.release_groups_promoted += len(promote)

        if create:
            await self._insert_release_groups(conn, create, result)

        unmapped_new: dict[tuple[int, str], str] = {}
        for p, aid in zip(page, artist_ids, strict=True):
            if p.release_group_mbid or not p.release_key:
                continue
            k = key_of(p, aid)
            if self.release_groups.find(None, k) is None:
                unmapped_new.setdefault(k, p.release_name or UNTITLED)
        if unmapped_new:
            await self._insert_release_groups(
                conn, [(k[0], title, k[1], None) for k, title in unmapped_new.items()], result
            )

        ids: list[int | None] = []
        for p, aid in zip(page, artist_ids, strict=True):
            if not p.release_group_mbid and not p.release_key:
                ids.append(None)
                continue
            ids.append(self.release_groups.find(p.release_group_mbid, key_of(p, aid)))
        return ids

    async def _insert_release_groups(
        self,
        conn: asyncpg.Connection,
        create: list[tuple[int, str, str, str | None]],
        result: PageResult,
    ) -> None:
        rows = await conn.fetch(
            """INSERT INTO release_group (artist_id, title, norm_key, mbid)
               SELECT * FROM unnest($1::int[], $2::text[], $3::text[], $4::uuid[])
               RETURNING release_group_id, mbid::text, artist_id, norm_key""",
            [c[0] for c in create],
            [c[1] for c in create],
            [c[2] for c in create],
            [c[3] for c in create],
        )
        for r in rows:
            self.release_groups.add(
                r["release_group_id"], r["mbid"], (r["artist_id"], r["norm_key"])
            )
        result.release_groups_created += len(rows)

    @staticmethod
    async def _aliases(
        conn: asyncpg.Connection,
        page: list[ParsedListen],
        artist_ids: list[int],
        album_artist_ids: list[int],
        rg_ids: list[int | None],
    ) -> None:
        artist_aliases = {(p.artist_name, aid) for p, aid in zip(page, artist_ids, strict=True)}
        artist_aliases |= {(p.artist.name, aid) for p, aid in zip(page, artist_ids, strict=True)}
        artist_aliases |= {
            (p.album_artist.name, aid) for p, aid in zip(page, album_artist_ids, strict=True)
        }
        await conn.execute(
            """INSERT INTO artist_alias (raw_name, artist_id, source)
               SELECT raw_name, artist_id, 'listenbrainz'
                 FROM unnest($1::text[], $2::int[]) AS u(raw_name, artist_id)
               ON CONFLICT DO NOTHING""",
            [a[0] for a in artist_aliases],
            [a[1] for a in artist_aliases],
        )
        rg_aliases = {
            (aid, p.release_name, rg)
            for p, aid, rg in zip(page, album_artist_ids, rg_ids, strict=True)
            if rg is not None and p.release_name
        }
        if rg_aliases:
            await conn.execute(
                """INSERT INTO release_group_alias (artist_id, raw_album, release_group_id, source)
                   SELECT artist_id, raw_album, release_group_id, 'listenbrainz'
                     FROM unnest($1::int[], $2::text[], $3::int[])
                          AS u(artist_id, raw_album, release_group_id)
                   ON CONFLICT DO NOTHING""",
                [a[0] for a in rg_aliases],
                [a[1] for a in rg_aliases],
                [a[2] for a in rg_aliases],
            )

    @staticmethod
    async def _listens(
        conn: asyncpg.Connection,
        page: list[ParsedListen],
        artist_ids: list[int],
        rg_ids: list[int | None],
    ) -> int:
        rows = await conn.fetch(
            """INSERT INTO listen (listened_at, artist_id, release_group_id, track_name, norm_title,
                                   artist_name, release_name, recording_mbid, release_mbid,
                                   recording_msid, duration_ms, client, inserted_at)
               SELECT * FROM unnest($1::timestamptz[], $2::int[], $3::int[], $4::text[], $5::text[],
                                    $6::text[], $7::text[], $8::uuid[], $9::uuid[], $10::uuid[],
                                    $11::int[], $12::text[], $13::timestamptz[])
               ON CONFLICT (listened_at, artist_id, norm_title) DO NOTHING
               RETURNING 1""",
            [p.listened_at for p in page],
            artist_ids,
            rg_ids,
            [p.track_name for p in page],
            [p.norm_title for p in page],
            [p.artist_name for p in page],
            [p.release_name for p in page],
            [p.recording_mbid for p in page],
            [p.release_mbid for p in page],
            [p.recording_msid for p in page],
            [p.duration_ms for p in page],
            [p.client for p in page],
            [p.inserted_at for p in page],
        )
        return len(rows)
