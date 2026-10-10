"""Album documents: liner notes and deep dives (docs/graph/COMPANION_SPEC.md section 7).

`render` turns a document's Markdown into its page (7.5): raw HTML in the text is escaped except
the track anchors, and each run of citation markers (`[c:<assertion_id>]`, `[p:<page id>]`) becomes
small source tags linking to what `citations` records for it. `load` stores a finished document
as the album's next ready version (the writing and fact-check happen outside, until Block F's
worker endpoints exist).
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import asyncpg
from markdown_it import MarkdownIt

from musicdata.db import connection
from musicdata.jobs.runs import JobFn, RunContext

TEMPLATE = Path(__file__).parent / "api" / "static" / "document.html"
KINDS = {"deep_dive": "Deep dive", "liner_notes": "Liner notes"}
MARKERS = re.compile(r"(?:\s*\[(?:c|p):[\w-]+\])+")
ONE = re.compile(r"\[(c|p):([\w-]+)\]")
ANCHOR = re.compile(r'<a id="track-(\d+)"></a>')


def _tags(run: str, citations: dict) -> str:
    seen, out = set(), []
    for kind, ident in ONE.findall(run):
        c = citations.get(f"{kind}:{ident}")
        if not c:
            continue
        key = (c.get("label"), c.get("url"))
        if key in seen:
            continue
        seen.add(key)
        out.append(
            f'<a class="src" href="{html.escape(c.get("url") or "#")}" '
            f'title="{html.escape(c.get("title") or "")}" target="_blank" rel="noopener">'
            f"{html.escape(c.get('label') or 'Source')}</a>"
        )
    return '<span class="srcs">' + "".join(out) + "</span>" if out else ""


def render(body_md: str, citations: dict, *, title: str, artist: str, facts: list[str],
           kind: str, checks: dict | None = None) -> str:  # fmt: skip
    body_md = body_md.split("\n## Sources", 1)[0]
    if body_md.startswith("# "):
        body_md = body_md.split("\n", 1)[1] if "\n" in body_md else ""
    tracks = re.findall(r"^### (\d+)\. (.+)$", body_md, re.M)
    # Markdown with raw HTML off: the text cannot inject markup; track anchors return below.
    body = MarkdownIt("commonmark", {"html": False}).render(body_md)
    body = MARKERS.sub(lambda m: _tags(m.group(0), citations), body)
    body = re.sub(r"\s*<p>&lt;a id=&quot;track-(\d+)&quot;&gt;&lt;/a&gt;</p>", "", body)
    body = re.sub(
        r"<h3>(\d+)\. (.+?)</h3>",
        lambda m: (
            f'<h3 id="track-{m.group(1)}"><span class="no">{m.group(1)}</span>{m.group(2)}</h3>'
        ),
        body,
    )
    body = re.sub(
        r"<h2>(.+?)</h2>",
        lambda m: (
            f'<h2 id="{re.sub(r"[^a-z]+", "-", m.group(1).lower()).strip("-")}">{m.group(1)}</h2>'
        ),
        body,
    )
    words = len(ONE.sub("", body_md).split())
    used = sorted(
        {i for k, i in ONE.findall(body_md) if k == "p"}, key=lambda x: (x[0].isdigit(), x)
    )
    pages = [citations[f"p:{i}"] for i in used if f"p:{i}" in citations]
    pages_html = "".join(
        f'<li><a href="{html.escape(c["url"])}" target="_blank" rel="noopener">'
        f"{html.escape(c.get('title') or c['url'])}</a></li>"
        for c in pages
    )
    claims = {i for i in re.findall(r"\[c:(\d+)\]", body_md)}
    check_line = ""
    if checks and checks.get("sentences"):
        check_line = (
            f"{checks['sentences']} cited sentences checked by a separate agent: "
            f"{checks.get('supported', 0)} supported as written, "
            f"{checks.get('rewritten', 0)} rewritten, {checks.get('removed', 0)} removed."
        )
    nav = "".join(f'<a href="#track-{n}"><span>{n}</span>{html.escape(t)}</a>' for n, t in tracks)
    fill = {
        "{{TITLE}}": html.escape(title), "{{ARTIST}}": html.escape(artist),
        "{{FACTS}}": "".join(f"<span>{html.escape(f)}</span>" for f in facts),
        "{{KIND}}": KINDS.get(kind, kind), "{{MINUTES}}": str(max(1, round(words / 230))),
        "{{TRACKS}}": nav, "{{PAGES}}": pages_html, "{{NCLAIMS}}": str(len(claims)),
        "{{CHECK}}": html.escape(check_line),
    }  # fmt: skip
    page = TEMPLATE.read_text("utf-8")
    for k, v in fill.items():
        page = page.replace(k, v)
    return page.replace("{{BODY}}", body)  # last, so the text's own braces stay as written


LATEST = """
SELECT d.document_id, d.kind, d.version, d.body_md, d.citations::text AS citations,
       d.checks::text AS checks, d.finished_at, rg.title, a.name AS artist, rg.first_release_year,
       (SELECT track_count FROM release_group_tracklist t WHERE t.release_group_id = rg.release_group_id)
         AS tracks
  FROM album_document d
  JOIN release_group rg USING (release_group_id)
  JOIN artist a ON a.artist_id = rg.artist_id
 WHERE d.release_group_id = $1 AND d.kind = $2 AND d.status = 'ready'
 ORDER BY d.version DESC LIMIT 1
"""


async def document_page(conn: asyncpg.Connection, release_group_id: int, kind: str) -> str | None:
    r = await conn.fetchrow(LATEST, release_group_id, kind)
    if r is None:
        return None
    facts = [str(x) for x in (r["first_release_year"],) if x]
    if r["tracks"]:
        facts.append(f"{r['tracks']} tracks")
    done = r["finished_at"]
    facts.append(f"version {r['version']} · {done:%B} {done.day}, {done.year}" if done else "")
    return render(
        r["body_md"] or "", json.loads(r["citations"]), title=r["title"], artist=r["artist"],
        facts=[f for f in facts if f], kind=kind, checks=json.loads(r["checks"]),
    )  # fmt: skip


def load(mbid: str, kind: str, body: Path, citations: Path, checks: Path | None,
         model: str | None, credits: int) -> JobFn:  # fmt: skip
    """Store a written, fact-checked document as the album's next ready version."""
    if kind not in KINDS:
        raise ValueError(f"kind is {' or '.join(KINDS)}, not {kind}")

    async def _run(ctx: RunContext) -> None:
        text = body.read_text("utf-8")
        cites = json.loads(citations.read_text("utf-8"))
        missing = sorted({f"{k}:{i}" for k, i in ONE.findall(text)} - set(cites))
        if missing:
            raise ValueError(f"markers without a citation: {missing[:10]}")
        async with connection(ctx.pool) as conn, conn.transaction():
            rg = await conn.fetchval(
                "SELECT release_group_id FROM release_group WHERE mbid = $1::uuid", mbid
            )
            if rg is None:
                raise ValueError(f"no release group with MBID {mbid} in this environment")
            await conn.execute(
                """UPDATE album_document SET status = 'superseded'
                    WHERE release_group_id = $1 AND kind = $2 AND status = 'ready'""",
                rg,
                kind,
            )
            doc = await conn.fetchrow(
                """INSERT INTO album_document (release_group_id, kind, version, status, finished_at,
                          model, body_md, citations, checks, firecrawl_credits)
                   VALUES ($1, $2, coalesce((SELECT max(version) FROM album_document
                                              WHERE release_group_id = $1 AND kind = $2), 0) + 1,
                           'ready', now(), $3, $4, $5::jsonb, $6::jsonb, $7)
                   RETURNING document_id, version""",
                rg,
                kind,
                model,
                text,
                json.dumps(cites),
                checks.read_text("utf-8") if checks else "{}",
                credits,
            )
        ctx.rows = 1
        ctx.notes.update(release_group_id=rg, kind=kind, document_id=doc["document_id"],
                         version=doc["version"], markers=len(cites))  # fmt: skip

    return _run
