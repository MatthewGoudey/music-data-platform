"""`musicdata recheck-tracklists`: ask MusicBrainz again about albums whose tracklist looks
cut short, and take the edition rule's answer when it changed.

A short tracklist leaves a trace: titles played on the album that match no track. Blonde
on Blonde held only its second LP (a Dutch release of the double album in two parts), so
every play of disc one was a stray. Albums with at least three such titles, and at least
40% as many as the tracklist has, are asked again; a hand-checked (`manual`) tracklist is
never touched. Re-run after a change to the edition rule (resolve/canonical.py).
"""

from __future__ import annotations

from musicdata.clients.musicbrainz import MusicBrainzClient, NotFoundError
from musicdata.config import get_settings
from musicdata.db import connection
from musicdata.jobs.runs import JobFn, RunContext
from musicdata.resolve.canonical import resolve_group
from musicdata.resolve.job import write_resolution

SUSPECTS = """
WITH extra AS (
  SELECT l.release_group_id, count(DISTINCT l.norm_title) AS titles
    FROM listen l
   WHERE NOT EXISTS (SELECT 1 FROM release_group_track t
                      WHERE t.release_group_id = l.release_group_id
                        AND (t.recording_mbid = l.recording_mbid OR t.norm_title = l.norm_title))
   GROUP BY 1)
SELECT rg.release_group_id, rg.mbid::text AS mbid, tl.release_mbid::text AS release_mbid,
       tl.track_count
  FROM extra e
  JOIN release_group rg USING (release_group_id)
  JOIN release_group_tracklist tl USING (release_group_id)
 WHERE tl.source = 'musicbrainz' AND rg.mbid IS NOT NULL
   AND e.titles >= 3 AND e.titles >= 0.4 * tl.track_count
 ORDER BY e.titles DESC
 LIMIT $1
"""


def recheck_tracklists(*, limit: int = 500) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        async with connection(ctx.pool) as conn:
            suspects = await conn.fetch(SUSPECTS, limit)
        changed: list[str] = []
        async with MusicBrainzClient(user_agent=get_settings().musicbrainz_user_agent) as mb:
            for s in suspects:
                try:
                    res = resolve_group(await mb.releases_of_group(s["mbid"]))
                except NotFoundError:
                    continue
                if res is None or res.release_mbid == s["release_mbid"]:
                    continue
                async with connection(ctx.pool) as conn:
                    await write_resolution(conn, s["release_group_id"], res)
                    await conn.execute(  # derive rebuilds the sessions of a changed group
                        "UPDATE release_group SET updated_at = now() WHERE release_group_id = $1",
                        s["release_group_id"],
                    )
                changed.append(f"{res.title}: {s['track_count']} → {len(res.tracks)} tracks")
            requests = mb.requests
        ctx.rows = len(changed)
        ctx.notes.update(
            suspects=len(suspects), changed=changed[:100], musicbrainz_requests=requests
        )

    return _run
