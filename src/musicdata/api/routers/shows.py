"""Chicago shows: upcoming listings, scored against the listening history, and interests.

match score (plan, "Scope"): ln(listens + 1) × min(distinct tracks / 5, 3) × recency, where
recency halves for every RECENCY_HALF_LIFE_YEARS since the artist was last played (ADR 0016).
A show scores as its best-matching performer, headliner or support.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from musicdata.api.deps import Format, FormatParam, Pool, render, require_token
from musicdata.db import connection

router = APIRouter(prefix="/shows", tags=["shows"], dependencies=[Depends(require_token)])

RECENCY_HALF_LIFE_YEARS = 2

SCORED = f"""
    SELECT sa.show_id, sa.clean_name, sa.role,
           ln(st.listens + 1) * least(st.distinct_tracks / 5.0, 3)
             * power(0.5, extract(epoch FROM now() - st.last_listened_at)
                          / ({RECENCY_HALF_LIFE_YEARS} * 365.25 * 86400)) AS score
      FROM show_artist sa JOIN artist_stat st USING (artist_id)
"""


@router.get("")
async def shows(
    pool: Pool,
    days: Annotated[int, Query(ge=1, le=365, description="The next N days.")] = 60,
    match: Annotated[bool, Query(description="Only shows with a performer you listen to.")] = False,
    venue: Annotated[str | None, Query(description="Venue name contains.")] = None,
    just_announced_days: Annotated[int | None, Query(ge=1, le=60)] = None,
    include_cancelled: bool = False,
    presales: Annotated[
        bool, Query(description="Only shows with a presale or public sale opening soon.")
    ] = False,
    sale_days: Annotated[int, Query(ge=1, le=90, description="'Soon' for presales=true.")] = 14,
    sort: Literal["date", "score", "sale"] | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    format: FormatParam = Format.compact,
):
    """Upcoming shows with the lineup, the best-matching performer and its score, and the
    next presale and public on-sale times. `match=true` sorts by score and
    `presales=true` by the next sale, unless `sort` says otherwise."""
    chosen = sort or ("sale" if presales else "score" if match else "date")
    order = {
        "score": "score DESC NULLS LAST, s.starts_at",
        "sale": "least(sale.next_presale, sale.on_sale) NULLS LAST, s.starts_at",
        "date": "s.starts_at",
    }[chosen]
    async with connection(pool) as conn:
        rows = await conn.fetch(
            f"""SELECT s.show_id, s.show_date, to_char(s.starts_at AT TIME ZONE 'America/Chicago', 'HH24:MI') AS time,
                       v.name AS venue, v.transit_min, v.walk_min, best.clean_name AS matched_artist,
                       round(best.score::numeric, 2) AS score,
                       (SELECT string_agg(sa.clean_name, ', ' ORDER BY sa.position)
                          FROM show_artist sa WHERE sa.show_id = s.show_id) AS lineup,
                       (SELECT ss.tickets_url FROM show_source ss
                         WHERE ss.show_id = s.show_id AND ss.tickets_url IS NOT NULL
                         ORDER BY ss.source = 'omr' DESC LIMIT 1) AS tickets,
                       to_char(sale.next_presale AT TIME ZONE 'America/Chicago',
                               'YYYY-MM-DD HH24:MI') AS next_presale,
                       sale.presale_name,
                       to_char(sale.on_sale AT TIME ZONE 'America/Chicago',
                               'YYYY-MM-DD HH24:MI') AS on_sale,
                       i.status AS interest, s.cancelled
                  FROM show s
                  JOIN venue v USING (venue_id)
                  LEFT JOIN LATERAL ({SCORED} WHERE sa.show_id = s.show_id
                                       AND NOT s.non_artist  -- tributes never match
                                     ORDER BY score DESC LIMIT 1) best ON true
                  LEFT JOIN LATERAL (
                      SELECT (SELECT min(ss.on_sale_at) FROM show_source ss
                               WHERE ss.show_id = s.show_id AND ss.on_sale_at > now()) AS on_sale,
                             p.start AS next_presale, p.name AS presale_name
                        FROM (SELECT NULL) one
                        LEFT JOIN LATERAL (
                            SELECT (x ->> 'start')::timestamptz AS start, x ->> 'name' AS name
                              FROM show_source ss, jsonb_array_elements(ss.presales) x
                             WHERE ss.show_id = s.show_id
                               AND (x ->> 'start')::timestamptz > now()
                             ORDER BY 1 LIMIT 1) p ON true
                  ) sale ON true
                  LEFT JOIN show_interest i ON i.show_id = s.show_id
                 WHERE s.starts_at >= now() - interval '6 hours'
                   AND s.starts_at < now() + make_interval(days => $1)
                   AND ($2::bool = false OR best.score IS NOT NULL)
                   AND ($3::text IS NULL OR v.name ILIKE '%' || $3 || '%')
                   AND ($4::int IS NULL OR s.first_seen_at > now() - make_interval(days => $4))
                   AND ($5::bool OR NOT s.cancelled)
                   AND ($7::bool = false
                        OR least(sale.next_presale, sale.on_sale)
                           < now() + make_interval(days => $8))
                 ORDER BY {order}
                 LIMIT $6""",
            days,
            match,
            venue,
            just_announced_days,
            include_cancelled,
            limit,
            presales,
            sale_days,
        )
    return render(rows, format)


class Interest(BaseModel):
    status: Literal["interested", "going"] = "interested"


@router.put("/{show_id}/interest")
async def set_interest(show_id: int, body: Interest, pool: Pool):
    async with connection(pool) as conn:
        if not await conn.fetchval("SELECT 1 FROM show WHERE show_id = $1", show_id):
            raise HTTPException(status_code=404, detail="no such show")
        await conn.execute(
            """INSERT INTO show_interest (show_id, status) VALUES ($1, $2)
               ON CONFLICT (show_id) DO UPDATE SET status = EXCLUDED.status, updated_at = now()""",
            show_id,
            body.status,
        )
    return {"show_id": show_id, "status": body.status}


@router.delete("/{show_id}/interest", status_code=204)
async def clear_interest(show_id: int, pool: Pool) -> None:
    async with connection(pool) as conn:
        await conn.execute("DELETE FROM show_interest WHERE show_id = $1", show_id)
