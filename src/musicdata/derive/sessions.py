"""Album sessions from listens: the plan's edge rules, in one pure function.

- One release group at a time; listens in time order; a gap over 30 minutes starts a
  new session.
- A listen counts toward a track of the standard tracklist when its recording MBID or
  its normalized title matches that track, or failing both, one of the loose title keys
  ("Pt. I" = "Part 1", no trailing "live at …", either half of a " / " track). Bonus
  tracks match nothing: they stay real listens but never advance completion.
- completion = distinct tracklist positions heard ÷ track count, capped at 1.0.
- full at ≥ 0.8; partial at ≥ 0.25 with at least 3 tracks; anything less is no session.
- Only albums and EPs with a resolved tracklist that are neither compilations nor box
  sets have sessions (`eligible`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from musicdata.identity import loose_title_keys

GAP = timedelta(minutes=30)
FULL_AT = 0.8
PARTIAL_AT = 0.25
PARTIAL_MIN_TRACKS = 3
SESSION_TYPES = ("Album", "EP")


@dataclass(frozen=True)
class Play:
    listened_at: datetime
    recording_mbid: str | None
    norm_title: str
    track_name: str | None = None


@dataclass(frozen=True)
class TrackRef:
    position: int
    recording_mbid: str | None
    norm_title: str
    title: str | None = None


@dataclass(frozen=True)
class Session:
    started_at: datetime
    ended_at: datetime
    tracks_played: int
    track_count: int
    completion: float
    session_type: str
    listen_count: int


def eligible(
    primary_type: str | None, is_compilation: bool, is_box_set: bool, track_count: int | None
) -> bool:
    return (
        primary_type in SESSION_TYPES
        and not is_compilation
        and not is_box_set
        and bool(track_count)
    )


class _Matcher:
    def __init__(self, tracks: list[TrackRef]) -> None:
        self.by_mbid: dict[str, int] = {}
        self.by_title: dict[str, int] = {}
        self.by_loose: dict[str, int] = {}
        for t in tracks:
            if t.recording_mbid:
                self.by_mbid.setdefault(t.recording_mbid, t.position)
            self.by_title.setdefault(t.norm_title, t.position)
        for t in tracks:  # after every exact title, so a loose key never shadows one
            for k in loose_title_keys(t.title or t.norm_title):
                self.by_loose.setdefault(k, t.position)

    def position(self, play: Play) -> int | None:
        if play.recording_mbid and play.recording_mbid in self.by_mbid:
            return self.by_mbid[play.recording_mbid]
        if play.norm_title in self.by_title:
            return self.by_title[play.norm_title]
        for k in sorted(
            loose_title_keys(play.track_name or play.norm_title), key=len, reverse=True
        ):
            if k in self.by_loose:
                return self.by_loose[k]
        return None


def _session(plays: list[Play], matcher: _Matcher, track_count: int) -> Session | None:
    heard = {p for p in (matcher.position(play) for play in plays) if p is not None}
    completion = min(1.0, len(heard) / track_count)
    if completion >= FULL_AT:
        kind = "full"
    elif completion >= PARTIAL_AT and len(heard) >= PARTIAL_MIN_TRACKS:
        kind = "partial"
    else:
        return None
    return Session(
        started_at=plays[0].listened_at,
        ended_at=plays[-1].listened_at,
        tracks_played=len(heard),
        track_count=track_count,
        completion=round(completion, 3),
        session_type=kind,
        listen_count=len(plays),
    )


def detect_sessions(plays: list[Play], tracks: list[TrackRef]) -> list[Session]:
    """Sessions for one release group. `plays` must be sorted by listened_at."""
    if not plays or not tracks:
        return []
    matcher = _Matcher(tracks)
    sessions: list[Session] = []
    run = [plays[0]]
    for play in plays[1:]:
        if play.listened_at - run[-1].listened_at > GAP:
            if s := _session(run, matcher, len(tracks)):
                sessions.append(s)
            run = []
        run.append(play)
    if s := _session(run, matcher, len(tracks)):
        sessions.append(s)
    return sessions
