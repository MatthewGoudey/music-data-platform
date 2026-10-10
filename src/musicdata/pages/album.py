"""The album page (docs/graph/COMPANION_SPEC.md 6.1): built from the brief when opened.

Header with status, Play and the document tabs; at a glance; tracks with their credits and
borrowings (the playing track highlighted); who made it, each name linked to its hub page with
"heard n of m"; connections; reading; and what the graph still lacks. An album the graph has not
baselined still renders, and opening it queues its baseline (3.6).
"""

from __future__ import annotations

import json
import re

import asyncpg

from musicdata.graph.importer import ensure_album
from musicdata.graph.queries import album_brief
from musicdata.identity import title_key
from musicdata.pages.common import esc, link, play_url, shell, site, src_tag
from musicdata.worker import document_states

PRODUCER = re.compile(r"produc", re.IGNORECASE)
LINEAGE_OUT = {
    "sounds_like": "compared to", "influenced_by": "influenced by", "covers": "covers",
    "interpolates": "borrows from", "samples": "samples", "tribute_to": "a tribute to",
    "named_after": "named after", "references": "names", "based_on": "based on",
    "arrangement_from": "follows the arrangement of",
}  # fmt: skip
LINEAGE_IN = {"sounds_like": "compared to it", "influenced_by": "influenced by it",
              "covers": "covers it", "interpolates": "borrows from it"}  # fmt: skip
GAP_TEXT = {
    "credits": "No credits read yet.",
    "recording": "Where it was recorded is not known yet.",
    "label": "No label recorded yet.",
    "people": "Who was in the band is not known yet.",
    "lineage": "No lineage read yet: nobody's comparisons, influences or covers.",
}


async def open_album(conn: asyncpg.Connection, release_group_id: int) -> bool:
    """3.6: an album opened without a baseline joins the `opened` slice for the next nightly
    import. True when it still needs one."""
    t = await ensure_album(conn, release_group_id)
    if t is None:
        return False
    baselined = await conn.fetchval(
        "SELECT baseline_at IS NOT NULL FROM graph_album WHERE entity_id = $1", t["entity_id"]
    )
    if baselined:
        return False
    await conn.execute(
        """INSERT INTO graph_album (entity_id, slices) VALUES ($1, ARRAY['opened'])
           ON CONFLICT (entity_id) DO UPDATE
              SET slices = CASE WHEN 'opened' = ANY(graph_album.slices) THEN graph_album.slices
                                ELSE graph_album.slices || 'opened'::text END""",
        t["entity_id"],
    )
    return True


async def _connections(conn: asyncpg.Connection, release_group_id: int) -> list[dict]:
    if not await conn.fetchval("SELECT to_regclass('graph_connection') IS NOT NULL"):
        return []
    top = await conn.fetchval(
        "SELECT top::text FROM graph_connection WHERE release_group_id = $1", release_group_id
    )
    return json.loads(top) if top else []


async def heard_counts(conn: asyncpg.Connection, ids: list[int]) -> dict[int, tuple[int, int]]:
    """For people and bands: (heard, all) albums they are credited on or released."""
    rows = await conn.fetch(
        """WITH alb AS (
               SELECT a.subject_id AS who, o.release_group_id AS rg
                 FROM assertion a JOIN entity o ON o.entity_id = a.object_id AND o.type = 'album'
                WHERE a.predicate = 'credited_on' AND a.status = 'accepted'
                  AND a.subject_id = ANY($1::bigint[])
               UNION
               SELECT e.entity_id, rg.release_group_id
                 FROM entity e JOIN release_group rg ON rg.artist_id = e.artist_id
                WHERE e.entity_id = ANY($1::bigint[]) AND e.artist_id IS NOT NULL)
           SELECT who, count(DISTINCT rg) AS m,
                  count(DISTINCT rg) FILTER (WHERE s.full_sessions > 0) AS n
             FROM alb LEFT JOIN release_group_stat s ON s.release_group_id = alb.rg
            WHERE rg IS NOT NULL GROUP BY who""",
        ids,
    )
    return {r["who"]: (int(r["n"]), int(r["m"])) for r in rows}


def _length(ms: int | None) -> str:
    if not ms:
        return ""
    s = round(ms / 1000)
    return f"{s // 60}:{s % 60:02d}"


def _roles(roles) -> str:
    items = roles if isinstance(roles, list) else [roles]
    return ", ".join(dict.fromkeys(str(r).lower() for r in items if r))


NOW_SCRIPT = r"""<script>
document.querySelectorAll("[data-request]").forEach(b => b.addEventListener("click", async () => {
  const t = new URLSearchParams(location.search).get("t") || "";
  b.disabled = true; b.textContent = "Requesting…";
  const r = await fetch(`${location.pathname.replace(/\/page$/, "/documents")}?t=${encodeURIComponent(t)}`,
    {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({kind: b.dataset.request})});
  if (r.ok) location.reload(); else { b.disabled = false; b.textContent = "Request failed — try again"; }
}));
document.getElementById("walk")?.addEventListener("click", async e => {
  const b = e.currentTarget, t = new URLSearchParams(location.search).get("t") || "";
  b.disabled = true; b.textContent = "Walking back…";
  const r = await fetch(`/graph/walk?from=${b.dataset.rg}&t=${encodeURIComponent(t)}`, {method: "POST"});
  if (!r.ok) { b.disabled = false; b.textContent = "Walk back failed — try again"; return; }
  const w = await r.json();
  if (!w.entries) { b.textContent = "Nothing older is connected yet"; return; }
  location.href = `/queue?t=${encodeURIComponent(t)}&profile=walk-back`;
});
const T = new URLSearchParams(location.search).get("t") || "";
let last = "";
async function poll() {
  if (document.hidden) return;
  try {
    const r = await fetch(`/queue/now-playing?t=${encodeURIComponent(T)}`);
    if (!r.ok) return;
    const now = await r.json();
    document.querySelectorAll("ol.tracks li.now").forEach(li => li.classList.remove("now"));
    const bar = document.getElementById("nowbar");
    if (!now || !now.title_key) { bar.hidden = true; return; }
    const li = document.querySelector(`ol.tracks li[data-key="${CSS.escape(now.title_key)}"]`);
    if (!li) { bar.hidden = true; return; }
    li.classList.add("now");
    bar.hidden = false;
    bar.textContent = `Playing now: ${now.track}`;
    if (now.title_key !== last) { li.scrollIntoView({behavior: "smooth", block: "center"}); last = now.title_key; }
  } catch (e) {}
}
poll();
setInterval(poll, 30000);
document.addEventListener("visibilitychange", poll);
</script>"""


async def album_page(conn: asyncpg.Connection, release_group_id: int, t: str) -> str | None:
    gathering = await open_album(conn, release_group_id)
    b = await album_brief(conn, release_group_id)
    if b is None:
        return None
    ident, ctx = b["identity"], b["context"]
    edges = b["edges"]
    docs = {k: v["status"] for k, v in (await document_states(conn, release_group_id)).items()}
    artist_hub = await conn.fetchval(
        """SELECT e.entity_id FROM entity e JOIN release_group rg ON rg.artist_id = e.artist_id
            WHERE rg.release_group_id = $1 AND e.type IN ('artist', 'person')
            ORDER BY e.entity_id LIMIT 1""",
        release_group_id,
    )
    labels = [e for e in edges.get("label", []) if e["predicate"] == "released_by"]
    stat = ctx.get("stat") or {}
    status = ctx.get("status", "unheard")
    sessions = stat.get("full_sessions") or 0
    status_text = {"heard": f"heard · {sessions} full play{'s' if sessions != 1 else ''}",
                   "started": "started", "unheard": "unheard"}[status]  # fmt: skip
    meta = " · ".join(
        str(x) for x in (ident.get("first_release_year"),
                         labels[0]["other"].get("name") if labels else None,
                         ident.get("primary_type")) if x
    )  # fmt: skip
    artist_html = (
        f'<a href="{esc(link(f"/entities/{artist_hub}/page", t))}">{esc(ident["artist"])}</a>'
        if artist_hub else esc(ident["artist"])
    )  # fmt: skip

    def tab(kind: str, label: str) -> str:
        """8.1 and 7.6: read it, or ask for it, or see where the request stands."""
        state = docs.get(kind)
        href = link(f"/albums/{release_group_id}/documents/{kind}", t)
        ask = (f'<button class="tab ask" type="button" data-request="{kind}">'
               f'{"Rewrite" if state == "out_of_date" else "Request"} {label.lower()}</button>')  # fmt: skip
        if state == "ready":
            return f'<a class="tab" href="{esc(href)}">{label}</a>'
        if state == "out_of_date":
            return f'<a class="tab" href="{esc(href)}">{label} · out of date</a>{ask}'
        if state == "requested":
            return f'<span class="tab off">{label} · requested, usually ready within a few hours</span>'
        if state == "writing":
            return f'<span class="tab off">{label} · being written</span>'
        if state == "failed":
            return f'<span class="tab off">{label} · failed</span>{ask}'
        return ask

    parts = [
        f"""<header class="box">
  <div class="meta">{esc(meta)}</div>
  <h1>{esc(ident["title"])}</h1>
  <div class="by">{artist_html}</div>
  <div class="actions"><span class="status {esc(status)}">{esc(status_text)}</span>
    <a class="btn play" href="{esc(play_url(ident["artist"], ident["title"]))}" target="_blank" rel="noopener">Play</a>
    <button class="btn" type="button" id="walk" data-rg="{release_group_id}">Walk back</button></div>
  <nav class="tabs" aria-label="Album pages"><span class="tab" aria-current="page">Album</span>{tab("deep_dive", "Deep dive")}</nav>
</header>
<div class="nowbar" id="nowbar" hidden></div>"""
    ]
    if gathering:
        parts.append(
            '<p class="lack">Gathering credits and sources for this album — check back tomorrow.</p>'
        )

    # At a glance
    rows = []
    places = [e for e in edges.get("place_period", []) if e["predicate"] == "recorded_at"]
    if places:
        rows.append(("Recorded at", "; ".join(
            f'{esc(e["other"].get("name"))}{" (" + esc(e["qualifiers"]["dates"]) + ")" if e["qualifiers"].get("dates") else ""}'
            f'{src_tag(e["sources"], e["source_url"], e["evidence"])}' for e in places[:4])))  # fmt: skip
    producers = [c for c in b["album_credits"] + [x for tr in b["tracks"] for x in tr["credits"]]
                 if PRODUCER.search(_roles(c["roles"]))]  # fmt: skip
    if producers:
        seen: dict[int, dict] = {c["entity_id"]: c for c in producers}
        rows.append(("Produced by", ", ".join(
            f'<a href="{esc(link(f"/entities/{c["entity_id"]}/page", t))}">{esc(c["name"])}</a>'
            for c in seen.values())))  # fmt: skip
    genres = [e for e in edges.get("genre_style", []) if e["predicate"] == "has_genre"]
    if genres:
        rows.append(("Genres", ", ".join(esc(e["other"].get("name")) for e in genres[:8])
                     + src_tag(genres[0]["sources"], genres[0]["source_url"])))  # fmt: skip
    for m in b["maps"]:
        c = m["coords"]
        if c.get("lane"):
            rows.append(
                (
                    "Atlas",
                    esc(
                        " · ".join(
                            str(x) for x in (c.get("lane"), c.get("zone"), c.get("priority")) if x
                        )
                    ),
                )
            )
    if labels:
        rows.append(("Label", ", ".join(
            f'<a href="{esc(link(f"/entities/{e["other"]["entity_id"]}/page", t))}">{esc(e["other"].get("name"))}</a>'
            f'{" " + esc(e["qualifiers"]["catalog"]) if e["qualifiers"].get("catalog") else ""}'
            for e in labels[:3])))  # fmt: skip
    if rows:
        parts.append(
            '<section><h2>At a glance</h2><div class="box">'
            + "".join(
                f'<div class="row"><span class="k">{k}</span><span>{v}</span></div>'
                for k, v in rows
            )
            + "</div></section>"
        )

    # Tracks
    if b["tracks"]:
        items = []
        for tr in b["tracks"]:
            notes = []
            for c in tr["credits"]:
                notes.append(
                    f'<a href="{esc(link(f"/entities/{c['entity_id']}/page", t))}">{esc(c["name"])}</a> {esc(_roles(c["roles"]))}'
                )
            for x in tr["lineage"]:
                verb = LINEAGE_OUT.get(x["predicate"], x["predicate"])
                notes.append(
                    f"{esc(verb)} {esc(x['other'])}{src_tag([], x['source_url'], x['evidence'])}"
                )
            note_html = f'<div class="notes">{"; ".join(notes)}</div>' if notes else ""
            items.append(
                f'<li id="track-{tr["position"]}" data-key="{esc(title_key(tr["title"]))}">'
                f'<span class="n">{tr["position"]}</span><span>{esc(tr["title"])}</span>'
                f'<span class="len">{_length(tr.get("length_ms"))}</span>{note_html}</li>'
            )
        parts.append(
            f'<section><h2>Tracks</h2><div class="box"><ol class="tracks">{"".join(items)}</ol></div></section>'
        )

    # Who made it
    people = {}
    for c in b["album_credits"]:
        people.setdefault(c["entity_id"], c)
    members = [e for e in edges.get("people", []) if e["predicate"] == "member_of"]
    ids = list(people) + [e["other"]["entity_id"] for e in members if e["other"].get("entity_id")]
    counts = await heard_counts(conn, ids) if ids else {}
    if people:
        lis = []
        for eid, c in people.items():
            n, m = counts.get(eid, (0, 0))
            mark = f' · <span class="meta">heard {n} of {m}</span>' if m > 1 else ""
            lis.append(
                f'<li><a href="{esc(link(f"/entities/{eid}/page", t))}">{esc(c["name"])}</a> — '
                f"{esc(_roles(c['roles']))}{src_tag(c['sources'], None)}{mark}</li>"
            )
        parts.append(
            f'<section><h2>Who made it</h2><div class="box"><ul class="plain">{"".join(lis)}</ul></div></section>'
        )

    # Connections
    lin = edges.get("lineage", [])
    out = [e for e in lin if e["direction"] == "out" and e["predicate"] in LINEAGE_OUT]
    inn = [e for e in lin if e["direction"] == "in" and e["predicate"] in LINEAGE_IN]
    conns = await _connections(conn, release_group_id)
    if out or inn or conns:
        lis = []
        for e in out:
            o = e["other"]
            name = (f'<a href="{esc(link(f"/entities/{o["entity_id"]}/page", t))}">{esc(o.get("name"))}</a>'
                    if o.get("entity_id") else esc(o.get("value")))  # fmt: skip
            via = e["qualifiers"].get("via")
            who = f"{esc(via)}: " if via else ""
            lis.append(
                f"<li>{who}{esc(LINEAGE_OUT[e['predicate']])} {name}{src_tag(e['sources'], e['source_url'])}"
                f'<div class="quote">{esc(e["evidence"])}</div></li>'
            )
        for e in inn:
            o = e["other"]
            lis.append(
                f'<li><a href="{esc(link(f"/entities/{o['entity_id']}/page", t))}">{esc(o.get("name"))}</a> '
                f"{esc(LINEAGE_IN[e['predicate']])}{src_tag(e['sources'], e['source_url'])}"
                f'<div class="quote">{esc(e["evidence"])}</div></li>'
            )
        for c in conns:
            lis.append(
                f"<li>{esc(c['text'])} "
                f'<a href="{esc(link(f"/albums/{c['heard']}/page", t))}">open</a></li>'
            )
        parts.append(
            f'<section><h2>Connections</h2><div class="box"><ul class="plain">{"".join(lis)}</ul></div></section>'
        )

    # Reading
    reading = []
    seen_urls = set()
    for p in b["pages"]:
        seen_urls.add(p["url"])
        reading.append(
            f'<li><a href="{esc(p["url"])}" target="_blank" rel="noopener">{esc(p.get("title") or p["url"])}</a> <span class="meta">{esc(site(p["url"]))}</span></li>'
        )
    for ln in b["links"]:
        if ln["url"] in seen_urls or ln["kind"] in ("musicbrainz", "wikidata", "streaming"):
            continue
        reading.append(
            f'<li><a href="{esc(ln["url"])}" target="_blank" rel="noopener">{esc(site(ln["url"]))}</a> <span class="meta">{esc(ln["kind"])}</span></li>'
        )
    if reading:
        parts.append(
            f'<section><h2>Reading</h2><div class="box"><ul class="plain">{"".join(reading)}</ul></div></section>'
        )

    # What the graph lacks
    lacks = [GAP_TEXT[g] for g in b["gaps"] if g in GAP_TEXT]
    if not b["tracks"]:
        lacks.append("No tracklist yet.")
    if lacks:
        tail = (
            ""
            if docs.get("deep_dive") in ("ready", "requested", "writing")
            else " Requesting a deep dive fills these in."
        )
        parts.append(
            f'<section><h2>What the graph lacks</h2><p class="lack">{" ".join(esc(x) for x in lacks)}{esc(tail)}</p></section>'
        )

    title = f"{ident['title']} · {ident['artist']}"
    return shell(title, "\n".join(parts), t, script=NOW_SCRIPT)
