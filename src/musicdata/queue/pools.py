"""Revisit pools (docs/QUEUE_SPEC.md section 9): the only candidates that come from
listening history. Pure: one row of session history in, at most one pool out.

| Pool       | Qualifies when                                   | Due when                       |
| spaced     | 1–3 full sessions, and on a list                  | 14 / 60 / 240 days after the last full session |
| abandoned  | ≥ 3 full sessions or ≥ 40 listens                 | last listen ≥ 180 days ago     |
| unfinished | ≥ 1 partial, 0 full, and on a list or ≥ 2 partials | last session ≥ 14 days ago     |

Every pool also wants the last session ≥ 30 days ago (unfinished: ≥ 14). An album that
qualifies for two pools goes to the first in fill order.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from musicdata.queue import config

ORDINAL = {1: "2nd", 2: "3rd", 3: "4th"}


@dataclass(frozen=True)
class Due:
    pool: str
    overdue_days: float
    reason: str


def _days(now: datetime, then: datetime | None) -> float | None:
    return None if then is None else (now - then).total_seconds() / 86400


def due(r: Mapping[str, object], now: datetime) -> Due | None:
    fulls, partials = int(r["full_sessions"]), int(r["partial_sessions"])
    listens = int(r["listens"] or 0)
    since_session = _days(now, r["last_session_at"])
    since_full = _days(now, r["last_full_at"])
    since_listen = _days(now, r["last_listened_at"])
    if since_session is None:
        return None
    if 1 <= fulls <= 3 and r["on_list"] and since_session >= config.REVISIT_REST_DAYS:
        wait = config.SPACED_DAYS[fulls]
        if since_full is not None and since_full >= wait:
            return Due(
                "spaced",
                since_full - wait,
                f"{ORDINAL[fulls]} listen due, last full play {int(since_full)} days ago",
            )
    if (
        (fulls >= config.ABANDONED_FULLS or listens >= config.ABANDONED_LISTENS)
        and since_session >= config.REVISIT_REST_DAYS
        and since_listen is not None
        and since_listen >= config.ABANDONED_DAYS
    ):
        return Due(
            "abandoned",
            since_listen - config.ABANDONED_DAYS,
            f"{listens} listens, none in {int(since_listen)} days",
        )
    if (
        partials >= 1
        and fulls == 0
        and (r["on_list"] or partials >= 2)
        and since_session >= config.UNFINISHED_DAYS
    ):
        best = r["best_completion"]
        run = f"best run {round(100 * float(best))}%, " if best is not None else ""
        return Due(
            "unfinished",
            since_session - config.UNFINISHED_DAYS,
            f"{run}last tried {int(since_session)} days ago",
        )
    return None
