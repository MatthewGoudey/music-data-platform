"""Reading batches (GRAPH_SPEC 7.4, 7.5): the Claude step's files in and out.

| Command | Does |
| --- | --- |
| `graph batch export` | the next albums whose facts step has run, as `data/graph/batches/<label>/` |
| `graph batch load-claims` | Claude's claims, names resolved, status `proposed` |
| `graph batch reader-input` | `reader_input.txt`: each text claim as a sentence, quote and source |
| `graph batch load-reader` | the reader's verdicts by claim label; the first one kept in `reader_first` |

Batch folders live on the laptop (`data/` is gitignored); the commands run there against an
environment (`musicdata --env dev graph batch …`).
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import asyncpg

from musicdata.clients.musicbrainz import MusicBrainzClient
from musicdata.config import get_settings
from musicdata.db import connection
from musicdata.graph import names
from musicdata.graph.claims import claim_key
from musicdata.graph.cues import cue_paragraphs
from musicdata.graph.entities import upsert_entity
from musicdata.graph.importer import slice_targets
from musicdata.graph.verify import ART, DB
from musicdata.identity import norm_key
from musicdata.jobs.runs import JobFn, RunContext

BATCHES = Path("data/graph/batches")
SENTENCES = {
    "sounds_like": "{s} is compared to, or said to sound like or build on, {o}",
    "influenced_by": "{s} was influenced or inspired by {o}",
    "covers": "{s} is a cover of {o}: a later recording of a song that {oa} recorded first",
    "samples": "{s} samples {o}",
    "credited_on": "{s} worked or played on {o}",
    "member_of": "{s} was a member of {o}",
    "associated_with": "{s} worked with {o} ({kind})",
    "toured_with": "{s} toured with or shared a bill with {o}",
    "performs_as": "{s} records or performs under the name {o}",
    "renamed_from": "{s} was earlier known as {o}",
    "interpolates": "{s} borrows the melody or lyric of {o}",
    "tribute_to": "{s} pays tribute or homage to {o}",
    "named_after": "{s} is named after {o}",
    "references": "{s} names or quotes {o}",
    "based_on": "{s} is based on, adapts or sets the words of {o}",
    "appears_in": "{s} was used in {o}",
    "relative_of": "{s} is a relative of {o}",
    "arrangement_from": "{s} follows the arrangement of {o}",
    "compiles": "{s} collects {o}",
    "companion_to": "{s} accompanies {o}",
    "has_genre": "{s} is described as {o}",
    "from_scene": "{s} came out of {o}",
    "based_in": "{s} is based in or comes from {o}",
    "recorded_at": "{s} was recorded at {o}",
    "released_by": "{s} was released by {o}",
}


async def batch_row(conn: asyncpg.Connection, label: str) -> asyncpg.Record:
    row = await conn.fetchrow("SELECT * FROM graph_batch WHERE label = $1", label)
    if row is None:
        raise ValueError(f"no batch {label}")
    return row


def folder(label: str, root: Path = Path(".")) -> Path:
    return root / BATCHES / label


# --- export -----------------------------------------------------------------------------------


def _header(f: asyncpg.Record, atlas_id: str) -> str:
    return (
        f"---\nurl: {f['url']}\ntitle: {f['title'] or ''}\nfetched: {f['fetched_at']:%Y-%m-%d}\n"
        f"entity: {atlas_id}\nschema_version: {f['schema_version']}\nfetch_id: {f['fetch_id']}\n"
        "---\n\n"
    )


def graph_batch_export(*, slice_name: str, size: int = 25, root: Path = Path(".")) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        async with connection(ctx.pool) as conn:
            targets = await slice_targets(conn, slice_name)
            ready = {
                r["entity_id"]: r
                for r in await conn.fetch(
                    """SELECT entity_id FROM graph_album
                        WHERE facts_at IS NOT NULL AND batch_id IS NULL"""
                )
            }
            picked = [t for t in targets if t["entity_id"] in ready][:size]
            if not picked:
                ctx.notes.update(slice=slice_name, albums=0, reason="no album ready")
                return
            async with conn.transaction():
                n = await conn.fetchval("SELECT count(*) FROM graph_batch") + 1
                label = f"P{n:02d}"
                batch_id = await conn.fetchval(
                    "INSERT INTO graph_batch (label, slice) VALUES ($1, $2) RETURNING batch_id",
                    label,
                    slice_name,
                )
                ids = [t["entity_id"] for t in picked]
                await conn.execute(
                    "UPDATE graph_album SET batch_id = $1 WHERE entity_id = ANY($2::bigint[])",
                    batch_id,
                    ids,
                )
            # an album's pages: those fetched for it, and any linked to it (a critic's post can
            # serve several albums)
            pairs = await conn.fetch(
                """SELECT DISTINCT a.album, f.fetch_id
                     FROM (SELECT DISTINCT ON (url) fetch_id, url, entity_id
                             FROM source_fetch WHERE ok ORDER BY url, fetched_at DESC) f
                     JOIN LATERAL (
                          SELECT f.entity_id AS album WHERE f.entity_id = ANY($1::bigint[])
                             AND NOT EXISTS (SELECT 1 FROM entity_link d  -- a dead link's page
                                              WHERE d.entity_id = f.entity_id AND d.url = f.url
                                                AND d.status <> 'ok')
                          UNION
                          SELECT l.entity_id FROM entity_link l
                           WHERE l.url = f.url AND l.entity_id = ANY($1::bigint[])
                             AND l.status = 'ok') a ON true""",
                ids,
            )
            fetches = await conn.fetch(
                """SELECT fetch_id, url, title, fetched_at, schema_version, mode, body
                     FROM source_fetch WHERE fetch_id = ANY($1::bigint[])""",
                sorted({p["fetch_id"] for p in pairs}),
            )
            known = await conn.fetch(
                """SELECT DISTINCT a.album_context, e.entity_id, e.type, e.name, a.predicate
                     FROM assertion a
                     JOIN entity e ON e.entity_id IN (a.subject_id, a.object_id)
                    WHERE a.album_context = ANY($1::bigint[]) AND e.entity_id <> a.album_context
                      AND a.status <> 'superseded'""",
                ids,
            )
            facets = {
                r["atlas_id"]: json.loads(r["facets"])
                for r in await conn.fetch(
                    """SELECT e.facets ->> 'atlas_id' AS atlas_id, e.facets::text AS facets
                         FROM list_entry e JOIN list l USING (list_id)
                        WHERE l.slug = 'v_atlas'
                          AND e.facets ->> 'atlas_id' = ANY($1::text[])""",
                    [t["atlas_id"] for t in picked],
                )
            }
        out = folder(label, root)
        (out / "pages").mkdir(parents=True, exist_ok=True)
        (out / "cues").mkdir(exist_ok=True)
        (out / "atlas").mkdir(exist_ok=True)
        atlas_of = {t["entity_id"]: t["atlas_id"] for t in picked}
        with (out / "albums.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["atlas_id", "artist", "album", "year", "priority", "entity_id"])
            for t in picked:
                w.writerow(
                    [t["atlas_id"], t["raw_artist"], t["raw_album"], t["first_release_year"] or "",
                     t["priority"] or "", t["entity_id"]]
                )  # fmt: skip
        albums_of: dict[int, list[str]] = defaultdict(list)
        for p in pairs:
            albums_of[p["fetch_id"]].append(atlas_of[p["album"]])
        with (out / "pages.csv").open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["atlas_id", "fetch_id", "url"])
            for f in fetches:
                for aid in sorted(albums_of[f["fetch_id"]]):
                    w.writerow([aid, f["fetch_id"], f["url"]])
        for f in fetches:
            aids = sorted(albums_of[f["fetch_id"]])
            (out / "pages" / f"{f['fetch_id']}.md").write_text(
                _header(f, " ".join(aids)) + (f["body"] or ""), encoding="utf-8"
            )
            artists = [t["raw_artist"] for t in picked if t["atlas_id"] in aids]
            (out / "cues" / f"{f['fetch_id']}.md").write_text(
                _header(f, " ".join(aids))
                + "\n\n".join(cue_paragraphs(f["body"] or "", artists))
                + "\n",
                encoding="utf-8",
            )
        for t in picked:
            fc = facets.get(t["atlas_id"], {})
            # the atlas is AI-written: its prose is never evidence (spec v5), so only the
            # pages it points at go to the reader of the batch
            note = {"source_urls": fc.get("source_urls")}
            (out / "atlas" / f"{t['atlas_id']}.json").write_text(
                json.dumps({"atlas_id": t["atlas_id"], **note}, indent=1, ensure_ascii=False),
                encoding="utf-8",
            )
        by_album: dict[str, list[dict]] = defaultdict(list)
        for k in known:
            by_album[atlas_of[k["album_context"]]].append(
                {"entity_id": k["entity_id"], "type": k["type"], "name": k["name"],
                 "predicate": k["predicate"]}
            )  # fmt: skip
        (out / "known.json").write_text(
            json.dumps(by_album, indent=1, ensure_ascii=False), encoding="utf-8"
        )
        counts = {"albums": len(picked), "pages": len(fetches)}
        async with connection(ctx.pool) as conn:
            await conn.execute(
                "UPDATE graph_batch SET counts = counts || $2::jsonb WHERE batch_id = $1",
                batch_id,
                json.dumps(counts),
            )
        ctx.rows = len(picked)
        ctx.notes.update(batch=label, slice=slice_name, folder=str(out), **counts)

    return _run


# --- load-claims and name resolution (7.5) ----------------------------------------------------


class Resolver:
    """Subject and object entities for Claude's claims, in the spec's order: an entity already
    linked to the album; for covers, the work's recordings; a MusicBrainz search; a joint
    credit by its parts. Every lookup is free (MusicBrainz at 1 request/second)."""

    def __init__(self, conn: asyncpg.Connection, mb: MusicBrainzClient) -> None:
        self.conn, self.mb = conn, mb
        self.known: dict[int, list[asyncpg.Record]] = {}
        self.searched: dict[tuple[str, str, str], int] = {}
        self.releases: dict[str, dict] = {}
        self.counts: dict[str, int] = defaultdict(int)

    async def upsert(
        self,
        type_: str,
        name: str,
        *,
        mbid: str | None = None,
        attrs: dict | None = None,
        status: str = "resolved",
        context: str = "",
    ) -> int:
        return await upsert_entity(
            self.conn, type_, name, mbid=mbid, attrs=attrs, status=status, context=context
        )

    async def linked(self, album_id: int) -> list[asyncpg.Record]:
        if album_id not in self.known:
            self.known[album_id] = await self.conn.fetch(
                """SELECT DISTINCT e.entity_id, e.type, e.norm_key, e.mbid
                     FROM assertion a JOIN entity e ON e.entity_id IN (a.subject_id, a.object_id)
                    WHERE a.album_context = $1 AND a.status <> 'superseded'""",
                album_id,
            )
        return self.known[album_id]

    async def entity(self, e: dict, album_id: int) -> int:
        type_, name = e["type"], e["name"]
        if e.get("atlas_id"):
            eid = await self.conn.fetchval(
                """SELECT entity_id FROM map_membership
                    WHERE map = 'v_atlas' AND coords ->> 'atlas_id' = $1""",
                e["atlas_id"],
            )
            if eid:
                return eid
        title = name.split(" – ", 1)[-1] if type_ in ("album", "recording", "work") else name
        k = norm_key(title)
        for r in await self.linked(album_id):
            same_type = r["type"] == type_ or (r["type"] in ART and type_ in ART)
            # Discogs files every credit as a person, bands too: a group named in a claim
            # skips an un-IDed person and resolves through MusicBrainz instead
            discogs_band = type_ == "artist" and r["type"] == "person" and r["mbid"] is None
            if same_type and r["norm_key"] == k and not discogs_band:
                self.counts["linked"] += 1
                return r["entity_id"]
        memo = (type_, name, e.get("artist") or "")
        if memo not in self.searched:
            if type_ in ART:
                self.searched[memo] = await self._artist(type_, name)
            elif type_ == "album":
                self.searched[memo] = await self._album(name, title, e)
            else:
                context = norm_key(e.get("artist") or "") if type_ in ("recording", "work") else ""
                attrs = {k: e[k] for k in ("artist", "year", "album") if e.get(k)}
                self.counts["unresolved"] += 1
                self.searched[memo] = await self.upsert(
                    type_, name, attrs=attrs, status="unresolved", context=context
                )
        return self.searched[memo]

    async def _artist(self, type_: str, name: str) -> int:
        hits = names.exact_artists(await self.mb.search_artists(name), name)
        if len(hits) == 1:
            a = hits[0]
            self.counts["resolved"] += 1
            t = "person" if a.get("type") == "Person" else "artist"
            return await self.upsert(t, name, mbid=a["id"], attrs=names.artist_attrs(a))
        if len(hits) > 1:
            self.counts["ambiguous"] += 1
            cands = [{"mbid": a["id"], **names.artist_attrs(a)} for a in hits]
            return await self.upsert(type_, name, attrs={"candidates": cands}, status="ambiguous")
        parts = []
        for part in names.joint_parts(name):
            found = names.exact_artists(await self.mb.search_artists(part), part)
            if len(found) == 1:
                parts.append({"name": part, "mbid": found[0]["id"],
                              **names.artist_attrs(found[0])})  # fmt: skip
        if parts:
            years = [p["year"] for p in parts if p.get("year")]
            self.counts["joint"] += 1
            return await self.upsert(
                "artist", name, attrs={"parts": parts, "year": min(years) if years else None}
            )
        self.counts["unresolved"] += 1
        return await self.upsert(type_, name, status="unresolved")

    async def _album(self, name: str, title: str, e: dict) -> int:
        artist = e.get("artist") or (name.split(" – ", 1)[0] if " – " in name else None)
        hits = names.exact_release_group(
            await self.mb.search_release_groups(title, artist_name=artist), title
        )
        context = norm_key(artist or "")
        if len(hits) == 1:
            rg = hits[0]
            self.counts["resolved"] += 1
            return await self.upsert(
                "album", name, mbid=rg["id"], context=context,
                attrs={"year": names.year(rg.get("first-release-date")), "artist": artist},
            )  # fmt: skip
        status = "ambiguous" if hits else "unresolved"
        self.counts[status] += 1
        attrs = {"artist": artist, "year": e.get("year")}
        if hits:
            attrs["candidates"] = [{"mbid": r["id"], "title": r["title"],
                                    "year": names.year(r.get("first-release-date"))} for r in hits]  # fmt: skip
        return await self.upsert("album", name, attrs=attrs, status=status, context=context)

    async def covers(self, c: dict, album_id: int) -> tuple[int, int] | None:
        """The subject and object recordings of a `covers` claim from the work on the album's
        canonical release (MusicBrainz browse by work)."""
        canon = await self.conn.fetchval(
            "SELECT notes ->> 'canonical_release' FROM graph_album WHERE entity_id = $1", album_id
        )
        title = (c.get("qualifiers") or {}).get("work") or c["object"]["name"].split(" – ")[-1]
        if not canon:
            return None
        if canon not in self.releases:
            self.releases[canon] = await self.mb.release(canon)
        w = names.find_work(names.release_works(self.releases[canon]), title)
        if w is None:
            return None
        work_id = await self.conn.fetchval(
            "SELECT entity_id FROM entity WHERE type = 'work' AND mbid = $1", w["work_mbid"]
        )
        sample = None
        if work_id:
            sample = await self.conn.fetchval(
                "SELECT attrs -> 'sample' FROM entity WHERE entity_id = $1", work_id
            )
        if sample is None:
            work = await self.mb.work(w["work_mbid"])
            data = names.work_sample(work, await self.mb.recordings_of_work(w["work_mbid"]))
            work_id = await self.upsert("work", data["title"] or title, mbid=w["work_mbid"],
                                        attrs=data)  # fmt: skip
            sample = data["sample"]
        elif isinstance(sample, str):
            sample = json.loads(sample)
        c.setdefault("qualifiers", {})["work_mbid"] = w["work_mbid"]
        by_artist = {names.match_key(r["artist"]): r for r in sample}
        ids = []
        for side in (c["subject"], c["object"]):
            r = by_artist.get(names.match_key(side.get("artist") or ""))
            if r is None:
                return None
            ids.append(
                await self.upsert(
                    "recording", f"{r['artist']} – {r['title']}", mbid=r["recording"],
                    context=norm_key(r["artist"]),
                    attrs={"artist": r["artist"], "year": names.year(r["first_release"]),
                           "work": w["work_mbid"]},
                )
            )  # fmt: skip
        self.counts["covers_resolved"] += 1
        return ids[0], ids[1]


def read_claims(paths: list[Path]) -> list[dict]:
    return [
        json.loads(ln)
        for p in paths
        for ln in p.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]


async def load_batch_claims(
    conn: asyncpg.Connection,
    mb: MusicBrainzClient,
    label: str,
    claims: list[dict],
    run_id: int | None,
) -> tuple[dict[str, int], dict[str, int]]:
    """Claude's claims into a batch: names resolved, status `proposed`. Returns the load
    counts and the name-resolution counts."""
    counts: dict[str, int] = defaultdict(int)
    batch = await batch_row(conn, label)
    albums = {
        r["atlas_id"]: r["entity_id"]
        for r in await conn.fetch(
            """SELECT m.coords ->> 'atlas_id' AS atlas_id, g.entity_id
                 FROM graph_album g JOIN map_membership m USING (entity_id)
                WHERE g.batch_id = $1 AND m.map = 'v_atlas'""",
            batch["batch_id"],
        )
    }
    res = Resolver(conn, mb)
    seq: dict[str, int] = defaultdict(int)
    atlas = [
        c.get("claim_id") or "?" for c in claims if str(c.get("source", "")).startswith("map:")
    ]
    if atlas:
        raise ValueError(f"the atlas is not a source of claims (spec v5): {atlas[:5]}")
    for c in claims:
        album_id = albums.get(c.get("album") or "")
        if album_id is None:
            raise ValueError(f"claim for album {c.get('album')} outside batch {label}")
        pair = await res.covers(c, album_id) if c["predicate"] == "covers" else None
        if pair is None:
            pair = (await res.entity(c["subject"], album_id),
                    await res.entity(c["object"], album_id))  # fmt: skip
        s, o = pair
        seq[c["album"]] += 1
        claim_label = c.get("claim_id") or f"{label}-{c['album']}-L{seq[c['album']]:03d}"
        evidence = c["evidence"]  # kept whole: over 300 characters fails the check
        url = c.get("source_url")
        fetch_id = await conn.fetchval(
            """SELECT fetch_id FROM source_fetch WHERE ok AND url = $1
                ORDER BY fetched_at DESC LIMIT 1""",
            (url or "").split("#")[0],
        )
        source = c["source"]
        if source == "wikipedia" and url and "wikipedia.org" not in url:
            source = "web:" + url.split("/")[2]
        async with conn.transaction():
            old = None
            if c.get("replaces"):
                old = await conn.fetchrow(
                    "SELECT assertion_id, reader_first FROM assertion WHERE claim_label = $1",
                    c["replaces"],
                )
                if old is None:
                    raise ValueError(f"{claim_label} replaces unknown {c['replaces']}")
            new = await conn.fetchval(
                """INSERT INTO assertion (claim_key, claim_label, replaces, subject_id,
                           predicate, object_id, qualifiers, source, extractor, basis,
                           direction, evidence, source_url, fetch_id, album_context,
                           batch_id, status, reader_first, asserted_by, pipeline_run_id)
                   VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11, $12,
                           $13, $14, $15, $16, 'proposed', $17,
                           'graph batch load-claims', $18)
                   ON CONFLICT DO NOTHING RETURNING assertion_id""",
                claim_key(s, c["predicate"], o, source, url, evidence),
                claim_label,
                old["assertion_id"] if old else None,
                s,
                c["predicate"],
                o,
                json.dumps(c.get("qualifiers") or {}, default=str),
                source,
                c.get("extractor") or "claude",
                c["basis"],
                c.get("direction"),
                evidence,
                url,
                fetch_id,
                album_id,
                batch["batch_id"],
                old["reader_first"] if old else None,
                run_id,
            )
            if old and new:  # retire the old claim only once its correction is in
                await conn.execute(
                    """UPDATE assertion SET status = 'superseded', updated_at = now()
                        WHERE assertion_id = $1""",
                    old["assertion_id"],
                )
            elif old:
                counts["correction_identical"] += (
                    1  # same edge, source and quote: nothing to replace
                )
        counts["loaded" if new else "already_loaded"] += 1
    await conn.execute(
        """UPDATE graph_batch SET status = 'claims_loaded',
                  counts = counts || $2::jsonb WHERE batch_id = $1""",
        batch["batch_id"],
        json.dumps({"claims": counts["loaded"] + counts["already_loaded"]}),
    )
    return counts, res.counts


def graph_batch_load_claims(*, label: str, files: list[Path]) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        claims = read_claims(files)
        ua = get_settings().musicbrainz_user_agent
        async with connection(ctx.pool) as conn, MusicBrainzClient(user_agent=ua) as mb:
            counts, named = await load_batch_claims(conn, mb, label, claims, ctx.run_id)
        ctx.rows = counts["loaded"]
        ctx.notes.update(batch=label, **counts, **{f"names_{k}": v for k, v in named.items()})

    return _run


# --- reader input and verdicts ----------------------------------------------------------------


def reader_line(c: dict, album_name: str) -> str:
    """One claim for the independent reader: a plain sentence, the quote, and the source."""

    def lab(e: dict) -> str:
        return e["name"] + (f" ({e['year']})" if e.get("year") else "")

    s, o, u = c["subject"], c["object"], c.get("source_url") or ""
    sent = SENTENCES[c["predicate"]].format(
        s=lab(s), o=lab(o), oa=o.get("artist") or o["name"],
        kind=(c.get("qualifiers") or {}).get("kind", "collaborator"),
    )  # fmt: skip
    if "wikipedia.org" in u:
        src = "Wikipedia article '" + u.split("#")[0].rsplit("/", 1)[1].replace("_", " ") + "'"
    elif u.startswith("atlas"):
        src = f"an AI-written atlas note about {album_name}"
    else:
        src = f"web page {u.split('#')[0]} (about {album_name})"
    return f'{c["claim_id"]} | CLAIM: {sent} | EVIDENCE: "{c["evidence"]}" | SOURCE: {src}'


def needs_reader(c: dict) -> bool:
    ex = c.get("extractor") or c["source"]
    return not (
        ex in (*DB, "firecrawl_json", "matt")
        or c["source"] in DB
        or c["evidence"].startswith("field:")
    )


def graph_batch_reader_input(*, label: str, root: Path = Path(".")) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        from musicdata.graph.verify_job import load_claims

        async with connection(ctx.pool) as conn:
            batch = await batch_row(conn, label)
            claims = await load_claims(conn, batch_id=batch["batch_id"])
            album_names = {
                r["atlas_id"]: f"{r['raw_artist']} – {r['raw_album']}"
                for r in await conn.fetch(
                    """SELECT e.facets ->> 'atlas_id' AS atlas_id, e.raw_artist, e.raw_album
                         FROM list_entry e JOIN list l USING (list_id) WHERE l.slug = 'v_atlas'"""
                )
            }
            lines = [
                reader_line(c, album_names.get(c["album"], c["album"]))
                for c in claims
                if needs_reader(c) and c["status"] not in ("rejected",)
                and not c["fails"] and c["reader"] is None  # judged claims are not sent again
            ]  # fmt: skip
            await conn.execute(
                """UPDATE graph_batch SET status = 'reader_exported',
                          counts = counts || $2::jsonb WHERE batch_id = $1""",
                batch["batch_id"],
                json.dumps({"reader_claims": len(lines)}),
            )
        out = folder(label, root)
        out.mkdir(parents=True, exist_ok=True)
        (out / "reader_input.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        ctx.rows = len(lines)
        ctx.notes.update(batch=label, reader_claims=len(lines), file=str(out / "reader_input.txt"))

    return _run


VERDICTS = ("SUPPORTS", "PARTIAL", "DOES_NOT_SUPPORT", "WRONG_DIRECTION", "NOT_A_CLAIM")


def graph_batch_load_reader(*, label: str, file: Path) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        verdicts = read_claims([file])
        bad = [v for v in verdicts if v.get("verdict") not in VERDICTS]
        if bad:
            raise ValueError(f"unknown verdicts: {[v.get('claim_id') for v in bad][:5]}")
        async with connection(ctx.pool) as conn, conn.transaction():
            batch = await batch_row(conn, label)
            labels = sorted({v["claim_id"] for v in verdicts})
            known = await conn.fetchval(
                "SELECT count(*) FROM assertion WHERE claim_label = ANY($1::text[]) AND batch_id = $2",
                labels,
                batch["batch_id"],
            )
            if known != len(labels):
                raise ValueError(
                    f"{len(labels) - known} verdicts name claims outside batch {label}"
                )
            await conn.executemany(
                """UPDATE assertion SET reader_verdict = $2, reader_reason = $3,
                          reader_first = coalesce(reader_first, $2), updated_at = now()
                    WHERE claim_label = $1 AND batch_id = $4""",
                [(v["claim_id"], v["verdict"], v.get("reason"), batch["batch_id"])
                 for v in verdicts],
            )  # fmt: skip
            await conn.execute(
                "UPDATE graph_batch SET status = 'reader_loaded' WHERE batch_id = $1",
                batch["batch_id"],
            )
        ctx.rows = len(verdicts)
        ctx.notes.update(batch=label, verdicts=len(verdicts))

    return _run
