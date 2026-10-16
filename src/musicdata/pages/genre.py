"""The genre page (queue spec 10a, v23): a genre's list albums, unheard first, what Matt has heard,
the genres that share its albums, and the past genres of the week. No written description: the
page shows counts and where each album is listed, nothing a model wrote about the genre."""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta

import asyncpg

from musicdata.pages.common import esc, link, shell
from musicdata.queue.genre_week import CHICAGO, entries, load_index, week_start, why_line
from musicdata.queue.genres import title

SHOWN = 120  # unheard albums listed before "and N more"
KIND_WORD = {"near": "near what you play", "adjacent": "next door to what you play",
             "stretch": "a stretch"}  # fmt: skip

ALBUMS = """
SELECT rg.release_group_id, rg.title, a.name AS artist, rg.first_release_year AS year
  FROM release_group rg JOIN artist a USING (artist_id)
 WHERE rg.release_group_id = ANY($1::int[])
"""
FINISHED_BETWEEN = """
SELECT DISTINCT release_group_id FROM album_session
 WHERE session_type = 'full' AND started_at >= $1 AND started_at < $2
   AND release_group_id = ANY($3::int[])
"""


def _start(week) -> datetime:
    return datetime.combine(week, time(0), CHICAGO)


def _li(a: asyncpg.Record, note: str, heard: bool, t: str) -> str:
    year = f'<span class="meta">{esc(a["year"])}</span> ' if a["year"] else ""
    mark = ' <span class="heard-mark" title="heard">✓</span>' if heard else ""
    where = f'<div class="meta">{esc(note)}</div>' if note else ""
    href = link(f"/albums/{a['release_group_id']}/page", t)
    return (f'<li>{year}<a href="{esc(href)}">{esc(a["title"])}</a> '
            f'<span class="meta">{esc(a["artist"])}</span>{mark}{where}</li>')  # fmt: skip


async def genre_page(conn: asyncpg.Connection, key: str, t: str) -> str:
    idx = await load_index(conn)
    name = idx.names.get(key, key)
    shown = title(name)
    has_weeks = await conn.fetchval("SELECT to_regclass('genre_week') IS NOT NULL")
    weeks = (
        await conn.fetch("SELECT * FROM genre_week ORDER BY week_start DESC LIMIT 13")
        if has_weeks
        else []
    )
    this_week = week_start()
    current = next((w for w in weeks if w["week_start"] == this_week and w["genre"] == key), None)

    rgs = idx.albums(key)
    info = {r["release_group_id"]: r for r in await conn.fetch(ALBUMS, rgs)}
    notes = dict(entries(idx, key))
    ordered = [rg for rg in notes if rg in info]
    unheard = [rg for rg in ordered if rg not in idx.heard]
    heard = [rg for rg in ordered if rg in idx.heard]

    parts = []
    meta = (
        f"Genre · {len(ordered)} list album{'s' if len(ordered) != 1 else ''} · heard {len(heard)}"
    )
    head = f'<header class="box"><div class="meta">{esc(meta)}</div><h1>{esc(shown)}</h1>'
    if current is not None:
        why = json.loads(current["why"]) if isinstance(current["why"], str) else current["why"]
        done = await conn.fetch(FINISHED_BETWEEN, _start(this_week),
                                _start(this_week + timedelta(days=7)), rgs)  # fmt: skip
        head += (
            f"<p><strong>Genre of the week</strong> · {esc(KIND_WORD[current['kind']])}. "
            f"{esc(why_line(current['kind'], why, idx.names))}</p>"
            f'<p class="meta">This week: finished {len(done)} of {len(ordered)}.</p>'
            f'<div class="actions"><a class="btn play" href="{esc(link("/queue", t, profile="genre-week"))}">'
            f"Open its queue</a>"
            f'<button class="btn" type="button" id="reroll">Pick another genre</button></div>'
        )
    head += "</header>"
    parts.append(head)

    if not ordered:
        parts.append('<p class="lack">No album on your lists carries this genre yet.</p>')
    if unheard:
        rows = "".join(_li(info[rg], notes[rg], False, t) for rg in unheard[:SHOWN])
        more = (f'<p class="meta">and {len(unheard) - SHOWN} more</p>'
                if len(unheard) > SHOWN else "")  # fmt: skip
        parts.append(f'<section><h2>Not heard yet · {len(unheard)}</h2><div class="box">'
                     f'<ul class="plain">{rows}</ul>{more}</div></section>')  # fmt: skip
    if heard:
        rows = "".join(_li(info[rg], notes[rg], True, t) for rg in heard)
        parts.append(f'<section><h2>Heard · {len(heard)}</h2><div class="box">'
                     f'<ul class="plain">{rows}</ul></div></section>')  # fmt: skip

    related = idx.related(key)
    if related:
        lis = ", ".join(
            f'<a href="{esc(link(f"/genres/{k}/page", t))}">{esc(idx.names.get(k, k))}</a>'
            f' <span class="meta">({n})</span>'
            for k, n in related
        )
        parts.append(
            f'<section><h2>Shares albums with</h2><div class="box"><p>{lis}</p></div></section>'
        )

    past = [w for w in weeks if w["week_start"] < this_week]
    if past:
        lis = []
        for w in past[:12]:
            done = await conn.fetch(FINISHED_BETWEEN, _start(w["week_start"]),
                                    _start(w["week_start"] + timedelta(days=7)),
                                    idx.albums(w["genre"]))  # fmt: skip
            when = f"{w['week_start'].strftime('%b')} {w['week_start'].day}"
            lis.append(
                f'<li><span class="meta">{when}</span> '
                f'<a href="{esc(link(f"/genres/{w['genre']}/page", t))}">{esc(title(w["name"]))}</a>'
                f' <span class="meta">· finished {len(done)}</span></li>'
            )
        parts.append(f'<section><h2>Past genres of the week</h2><div class="box">'
                     f'<ul class="plain">{"".join(lis)}</ul></div></section>')  # fmt: skip

    script = """<script>
const b = document.getElementById("reroll");
if (b) b.addEventListener("click", async () => {
  b.disabled = true; b.textContent = "Picking…";
  const t = new URLSearchParams(location.search).get("t") || "";
  const r = await fetch(`/genre-week/reroll?t=${encodeURIComponent(t)}`, {method: "POST"});
  if (!r.ok) { b.disabled = false; b.textContent = "Try again"; return; }
  const j = await r.json();
  location.href = `/genres/${encodeURIComponent(j.genre)}/page?t=${encodeURIComponent(t)}`;
});
</script>"""
    return shell(shown, "\n".join(parts), t, script=script)
