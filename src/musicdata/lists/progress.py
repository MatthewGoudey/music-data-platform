"""Progress through the lists (docs/QUEUE_SPEC.md sections 6 and 12): heard / total per list,
per atlas lane or per atlas zone, read from `list_entry_status`. Used by `GET /gaps`,
`GET /lists`, the page's progress strip and checks L4–L5."""

from __future__ import annotations

from typing import Literal

import asyncpg

By = Literal["list", "lane", "zone"]

GROUPS: dict[str, tuple[str, str]] = {
    # by: (select columns, group by)
    "list": ("l.slug AS list, l.name, l.goal", "l.slug, l.name, l.goal"),
    "lane": ("e.lane_id AS lane, al.name, e.zone", "e.lane_id, al.name, e.zone"),
    "zone": ("e.zone", "e.zone"),
}

PROGRESS = """
SELECT {select},
       count(*) AS total,
       count(st.entry_id) AS resolved,
       count(*) FILTER (WHERE st.status = 'heard') AS heard,
       count(*) FILTER (WHERE st.status = 'started') AS started,
       count(*) FILTER (WHERE st.status = 'unheard') AS unheard,
       count(*) - count(st.entry_id) AS needs_matching,
       round(100.0 * count(*) FILTER (WHERE st.status = 'heard') / count(*), 1) AS pct_heard
  FROM list l
  JOIN list_entry e USING (list_id)
  LEFT JOIN list_entry_status st USING (entry_id)
  LEFT JOIN atlas_lane al ON al.lane_id = e.lane_id
 WHERE e.review_status = 'accepted'
   AND ($1::text IS NULL OR l.slug = $1)
   AND ($2::text IS NULL OR l.goal = $2)
   AND ($3::text IS NULL OR e.lane_id = $3)
   AND ($4::text IS NULL OR e.zone = $4)
   AND ($5::bool OR e.lane_id IS NOT NULL)
 GROUP BY {group}
 ORDER BY {group}
"""


async def progress(
    conn: asyncpg.Connection,
    by: By = "list",
    *,
    slug: str | None = None,
    goal: str | None = None,
    lane: str | None = None,
    zone: str | None = None,
) -> list[asyncpg.Record]:
    select, group = GROUPS[by]
    return await conn.fetch(
        PROGRESS.format(select=select, group=group), slug, goal, lane, zone, by == "list"
    )
