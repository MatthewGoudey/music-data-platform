"""Hub pages (docs/graph/COMPANION_SPEC.md 6.2): a person or band, a label or studio, a song.

Person or band: albums by year with heard marks and "heard n of m", roles on each; bands and
projects; people worked with. Label or studio: albums by year with heard marks. Song: its
recordings and the covers and borrowings that tie them, with Play per version. Follow arrives with
Block E's second part.
"""

from __future__ import annotations

import json
from collections import defaultdict

import asyncpg

from musicdata.graph.queries import entity_detail
from musicdata.pages.common import esc, link, play_url, shell, src_tag

ALBUM_EDGES = """
SELECT DISTINCT ON (o.entity_id) o.entity_id, o.name, o.release_group_id,
       coalesce(rg.first_release_year, (o.attrs ->> 'year')::int) AS year,
       coalesce(ar.name, o.attrs ->> 'artist') AS artist,
       coalesce(s.full_sessions, 0) AS sessions, a.predicate, a.qualifiers::text AS qualifiers,
       a.source, a.source_url, a.evidence
  FROM assertion a
  JOIN entity o ON o.entity_id = CASE WHEN a.subject_id = $1 THEN a.object_id ELSE a.subject_id END
  LEFT JOIN release_group rg ON rg.release_group_id = o.release_group_id
  LEFT JOIN artist ar ON ar.artist_id = rg.artist_id
  LEFT JOIN release_group_stat s ON s.release_group_id = o.release_group_id
 WHERE a.status = 'accepted' AND o.type = 'album'
   AND ((a.subject_id = $1 AND a.predicate = 'credited_on')
        OR (a.object_id = $1 AND a.predicate IN ('released_by', 'recorded_at')))
 ORDER BY o.entity_id, a.assertion_id
"""
OWN_ALBUMS = """
SELECT e.entity_id, rg.title AS name, rg.release_group_id, rg.first_release_year AS year,
       ar.name AS artist, coalesce(s.full_sessions, 0) AS sessions
  FROM release_group rg
  JOIN artist ar ON ar.artist_id = rg.artist_id
  LEFT JOIN entity e ON e.release_group_id = rg.release_group_id AND e.type = 'album'
  LEFT JOIN release_group_stat s ON s.release_group_id = rg.release_group_id
 WHERE rg.artist_id = $1 AND rg.primary_type = 'Album'
   AND NOT ('Compilation' = ANY(coalesce(rg.secondary_types, '{}'))
            OR 'Live' = ANY(coalesce(rg.secondary_types, '{}')))
"""
TYPE_WORD = {"person": "Person", "artist": "Band or artist", "label": "Label", "place": "Studio",
             "work": "Song", "recording": "Recording", "album": "Album", "area": "Place",
             "genre": "Genre"}  # fmt: skip
PEOPLE_WORDS = {"member_of": "member of", "performs_as": "performs as", "renamed_from": "earlier known as",
                "associated_with": "worked with", "toured_with": "toured with",
                "relative_of": "family of"}  # fmt: skip


def _roles(qualifiers: str | None) -> str:
    q = json.loads(qualifiers or "{}")
    r = q.get("role") or q.get("instrument") or ""
    items = r if isinstance(r, list) else [r]
    return ", ".join(dict.fromkeys(str(x).lower() for x in items if x))


def _album_li(a: dict, t: str) -> str:
    target = (link(f"/albums/{a['release_group_id']}/page", t) if a.get("release_group_id")
              else link(f"/entities/{a['entity_id']}/page", t))  # fmt: skip
    mark = ' <span class="heard-mark" title="heard">✓</span>' if a.get("sessions") else ""
    role = f' <span class="meta">— {esc(a["role"])}</span>' if a.get("role") else ""
    by = f' <span class="meta">{esc(a["artist"])}</span>' if a.get("artist") else ""
    year = f'<span class="meta">{esc(a.get("year") or "")}</span> ' if a.get("year") else ""
    return f'<li>{year}<a href="{esc(target)}">{esc(a["name"])}</a>{by}{role}{mark}</li>'


async def entity_page(conn: asyncpg.Connection, entity_id: int, t: str) -> str | None:
    d = await entity_detail(conn, entity_id)
    if d is None:
        return None
    if d["type"] == "album" and d.get("release_group_id"):
        return None  # albums have their own page; the router redirects
    kind = d["type"]
    attrs = d["attrs"]
    parts = []
    sub = " · ".join(str(x) for x in (TYPE_WORD.get(kind, kind), attrs.get("area"),
                                       attrs.get("year"), attrs.get("disambiguation")) if x)  # fmt: skip
    follow = ""
    if kind in ("person", "artist", "label", "place") and await conn.fetchval(
        "SELECT to_regclass('graph_follow') IS NOT NULL"
    ):
        on = await conn.fetchval("SELECT 1 FROM graph_follow WHERE entity_id = $1", entity_id)
        follow = (
            f'<div class="actions"><button class="btn{" play" if not on else ""}" type="button" '
            f'data-follow="{"unfollow" if on else "follow"}">{"Following ✓" if on else "Follow"}</button>'
            f'<a class="btn" href="{esc(link("/queue", t, profile="following"))}">Open the following queue</a></div>'
        )
    parts.append(
        f'<header class="box"><div class="meta">{esc(sub)}</div><h1>{esc(d["name"])}</h1>{follow}</header>'
    )

    albums: dict[int, dict] = {}
    for r in await conn.fetch(ALBUM_EDGES, entity_id):
        albums[r["entity_id"]] = dict(r) | {"role": _roles(r["qualifiers"])}
    if d.get("artist_id"):
        for r in await conn.fetch(OWN_ALBUMS, d["artist_id"]):
            key = r["entity_id"] or -r["release_group_id"]
            albums.setdefault(key, dict(r) | {"role": ""})
    if albums:
        ordered = sorted(albums.values(), key=lambda a: (a.get("year") or 9999, a["name"] or ""))
        heard = sum(1 for a in ordered if a.get("sessions"))
        parts.append(
            f'<section><h2>Albums · heard {heard} of {len(ordered)}</h2><div class="box"><ul class="plain">'
            + "".join(_album_li(a, t) for a in ordered)
            + "</ul></div></section>"
        )

    # Bands, projects and people
    rel = []
    for facet in ("people", "lineage"):
        for e in d["edges"].get(facet, []):
            if e["predicate"] not in PEOPLE_WORDS:
                continue
            o = e["other"]
            if not o.get("entity_id"):
                continue
            word = PEOPLE_WORDS[e["predicate"]]
            if e["predicate"] == "member_of" and e["direction"] == "in":
                word = "has member"
            if e["predicate"] in ("performs_as", "renamed_from") and e["direction"] == "in":
                word = {"performs_as": "project of", "renamed_from": "later known as"}[
                    e["predicate"]
                ]
            kindq = e["qualifiers"].get("kind") or e["qualifiers"].get("instrument") or ""
            extra = (
                f' <span class="meta">({esc(kindq)})</span>'
                if kindq and isinstance(kindq, str)
                else ""
            )
            rel.append((word, f'<a href="{esc(link(f"/entities/{o["entity_id"]}/page", t))}">{esc(o.get("name"))}</a>'
                        f'{extra}{src_tag(e["sources"], e["source_url"], e["evidence"])}'))  # fmt: skip
    if rel:
        grouped: dict[str, list[str]] = defaultdict(list)
        for w, h in rel:
            grouped[w].append(h)
        parts.append('<section><h2>Bands, projects and people</h2><div class="box">' + "".join(
            f'<div class="row"><span class="k">{esc(w)}</span><span>{", ".join(hs)}</span></div>'
            for w, hs in grouped.items()) + "</div></section>")  # fmt: skip

    # Songs: recordings, covers and borrowings
    if kind in ("work", "recording"):
        lis = []
        for e in d["edges"].get("lineage", []):
            if e["predicate"] not in ("covers", "interpolates", "samples", "arrangement_from"):
                continue
            o = e["other"]
            verb = {"covers": "covers", "interpolates": "borrows from", "samples": "samples",
                    "arrangement_from": "follows the arrangement of"}[e["predicate"]]  # fmt: skip
            line = (f"this {verb} {esc(o.get('name'))}" if e["direction"] == "out"
                    else f"{esc(o.get('name'))} {verb} this")  # fmt: skip
            artist, _, song = (o.get("name") or "").partition(" – ")
            play = f' <a class="src" href="{esc(play_url(artist, song or artist))}" target="_blank" rel="noopener">Play</a>'
            lis.append(
                f"<li>{line}{src_tag(e['sources'], e['source_url'], e['evidence'])}{play}</li>"
            )
        if lis:
            parts.append(
                f'<section><h2>Versions</h2><div class="box"><ul class="plain">{"".join(lis)}</ul></div></section>'
            )

    if d["links"]:
        lis = "".join(
            f'<li><a href="{esc(ln["url"])}" target="_blank" rel="noopener">{esc(ln["kind"])}</a></li>'
            for ln in d["links"] if ln.get("status", "ok") == "ok"
        )  # fmt: skip
        parts.append(
            f'<section><h2>Links</h2><div class="box"><ul class="plain">{lis}</ul></div></section>'
        )
    if len(parts) == 1:
        parts.append('<p class="lack">The graph knows this name but nothing more about it yet.</p>')
    script = f"""<script>
document.querySelectorAll("[data-follow]").forEach(b => b.addEventListener("click", async () => {{
  b.disabled = true;
  const t = new URLSearchParams(location.search).get("t") || "";
  const r = await fetch(`/entities/{entity_id}/${{b.dataset.follow}}?t=${{encodeURIComponent(t)}}`, {{method: "POST"}});
  if (r.ok) location.reload(); else {{ b.disabled = false; b.textContent = "Try again"; }}
}}));
</script>"""
    return shell(d["name"], "\n".join(parts), t, script=script)


LIST_SEARCH = r"""
WITH hits AS (
    SELECT e.release_group_id, e.artist_key, e.album_key, e.raw_album, e.raw_artist, e.year, l.name
      FROM list_entry e JOIN list l USING (list_id)
     WHERE e.review_status <> 'rejected'
       AND (e.raw_album ILIKE '%' || $1 || '%' OR e.raw_artist ILIKE '%' || $1 || '%'
            OR ($2 <> '' AND (e.album_key LIKE '%' || $2 || '%' OR e.artist_key LIKE '%' || $2 || '%')))
    UNION ALL  -- albums heard but on no list
    SELECT rg.release_group_id, a.norm_key, rg.norm_key, rg.title, a.name, rg.first_release_year, NULL
      FROM release_group rg JOIN artist a USING (artist_id)
     WHERE rg.title ILIKE '%' || $1 || '%' OR a.name ILIKE '%' || $1 || '%'
)
SELECT h.release_group_id,
       coalesce(min(rg.title), min(h.raw_album)) AS title,
       coalesce(min(a.name), min(h.raw_artist)) AS artist,
       coalesce(min(rg.first_release_year), min(h.year)) AS year,
       array_remove(array_agg(DISTINCT h.name), NULL) AS lists
  FROM hits h
  LEFT JOIN release_group rg ON rg.release_group_id = h.release_group_id
  LEFT JOIN artist a ON a.artist_id = rg.artist_id
 GROUP BY h.release_group_id,
          CASE WHEN h.release_group_id IS NULL THEN h.artist_key || '|' || h.album_key END
 ORDER BY count(h.name) DESC, 2
 LIMIT 80
"""


async def _list_albums(conn: asyncpg.Connection, q: str, t: str, shown: set[int]) -> list[str]:
    """Every album on Matt's lists (and every album he has heard) whose title or artist matches,
    whether or not the graph has met it; one an album page cannot show yet (no match to
    MusicBrainz) is listed without a link."""
    from musicdata.identity import norm_key

    lis = []
    for r in await conn.fetch(LIST_SEARCH, q, norm_key(q) or ""):
        if r["release_group_id"] in shown:
            continue
        year = f" ({r['year']})" if r["year"] else ""
        n = len(r["lists"])
        lists = f" · on {n} list{'s' if n > 1 else ''}" if n else ""
        meta = (f'<span class="meta" title="{esc(", ".join(r["lists"]))}">'
                f'Album · {esc(r["artist"])}{year}{lists}</span>')  # fmt: skip
        if r["release_group_id"]:
            href = esc(link(f"/albums/{r['release_group_id']}/page", t))
            lis.append(f'<li><a href="{href}">{esc(r["title"])}</a> {meta}</li>')
        else:
            lis.append(f"<li>{esc(r['title'])} {meta} "
                       f'<span class="meta">· not matched yet</span></li>')  # fmt: skip
    return lis


# The best-known names first: a word of the name starts with what was typed, ranked by the
# accepted claims that touch it (Neil Young before a session player named Neil).
SUGGEST_NAMES = r"""
WITH c AS (
    SELECT entity_id, type, name FROM entity
     WHERE type IN ('person', 'artist', 'label', 'place')
       AND (name ILIKE $1 || '%' OR name ILIKE '% ' || $1 || '%')
     LIMIT 200
)
SELECT c.entity_id, c.type, c.name,
       (SELECT count(*) FROM assertion a
         WHERE a.status = 'accepted' AND (a.subject_id = c.entity_id OR a.object_id = c.entity_id)) AS n
  FROM c ORDER BY n DESC, c.name LIMIT 3
"""


async def suggest(conn: asyncpg.Connection, q: str, t: str, n: int = 8) -> list[dict]:
    """Autocomplete: people, bands, labels and studios the graph knows, then albums from the
    lists and listening, names that start with what was typed first."""
    from musicdata.identity import norm_key

    q = q.strip()
    if len(q) < 2:
        return []
    lo = q.casefold()
    out = []
    for r in await conn.fetch(SUGGEST_NAMES, q):
        out.append({"label": r["name"], "meta": TYPE_WORD.get(r["type"], r["type"]),
                    "href": link(f"/entities/{r['entity_id']}/page", t)})  # fmt: skip
    for s in out:
        s["first"] = s["label"].casefold().startswith(lo)
    albums = []
    for r in await conn.fetch(LIST_SEARCH, q, norm_key(q) or ""):
        year = f" · {r['year']}" if r["year"] else ""
        href = (link(f"/albums/{r['release_group_id']}/page", t) if r["release_group_id"]
                else link("/pages/search", t, q=r["title"]))  # fmt: skip
        first = r["title"].casefold().startswith(lo) or r["artist"].casefold().startswith(lo)
        albums.append({"label": r["title"], "meta": f"{r['artist']}{year}", "href": href,
                       "first": first})  # fmt: skip
    albums.sort(key=lambda s: not s["first"])  # stable: list count order within each group
    return [{k: v for k, v in s.items() if k != "first"} for s in (out + albums)[:n]]


async def search_page(conn: asyncpg.Connection, q: str, t: str) -> str:
    from musicdata.graph.queries import search_entities

    q = q.strip()
    lis = []
    shown: set[int] = set()  # albums already listed through the graph
    if q:
        for r in await search_entities(conn, q, None, 40):
            if r["type"] in ("lane", "map", "list", "tag"):
                continue
            if r["release_group_id"]:
                shown.add(r["release_group_id"])
            target = (link(f"/albums/{r['release_group_id']}/page", t) if r["release_group_id"]
                      else link(f"/entities/{r['entity_id']}/page", t))  # fmt: skip
            lis.append(
                f'<li><a href="{esc(target)}">{esc(r["name"])}</a> '
                f'<span class="meta">{esc(TYPE_WORD.get(r["type"], r["type"]))}</span></li>'
            )
        lis += await _list_albums(conn, q, t, shown)
    body = (f'<header class="box"><h1>Search</h1><div class="meta">{esc(q) or "Type a name above."}</div></header>'
            + (f'<section><div class="box"><ul class="plain">{"".join(lis)}</ul></div></section>' if lis
               else (f'<p class="lack">Nothing found for “{esc(q)}”.</p>' if q else "")))  # fmt: skip
    return shell(f"Search · {q}" if q else "Search", body, t)
