"""Pick a release group's canonical release and flatten its tracklist. Pure functions.

The edition rule (plan, "Edge rules"): the canonical release is official, dated within
12 months of the group's first release, carries no edition marker, and has the fewest
tracks; ties go to the earliest date. Each filter is skipped when it would leave nothing,
so a group of bootlegs or undated releases still gets a tracklist. Video media (a DVD in
a CD+DVD edition) never count as tracks.
"""

from __future__ import annotations

from dataclasses import dataclass

from musicdata.identity import album_key, has_edition_marker, title_key

VIDEO_FORMATS = {"DVD", "DVD-Video", "Blu-ray", "VHS", "HD-DVD", "VCD", "SVCD", "UMD"}
BOX_SET_TRACKS = 30


@dataclass(frozen=True)
class Track:
    position: int
    title: str
    norm_title: str
    recording_mbid: str | None
    length_ms: int | None


@dataclass(frozen=True)
class Resolution:
    title: str
    norm_key: str
    primary_type: str | None
    secondary_types: tuple[str, ...]
    first_release_year: int | None
    artist_mbid: str | None
    artist_name: str | None
    release_mbid: str
    tracks: tuple[Track, ...]

    @property
    def is_compilation(self) -> bool:
        return "Compilation" in self.secondary_types

    @property
    def is_box_set(self) -> bool:
        return len(self.tracks) > BOX_SET_TRACKS


def _audio_media(release: dict) -> list[dict]:
    return [m for m in release.get("media") or [] if m.get("format") not in VIDEO_FORMATS]


def _track_count(release: dict) -> int:
    return sum(len(m.get("tracks") or []) for m in _audio_media(release))


def _months(date: str | None) -> int | None:
    """'1999-06-14' → months since year 0; None when there is no year."""
    if not date or not date[:4].isdigit():
        return None
    month = int(date[5:7]) if len(date) >= 7 and date[5:7].isdigit() else 1
    return int(date[:4]) * 12 + month


def _narrow(candidates: list[dict], keep) -> list[dict]:
    kept = [r for r in candidates if keep(r)]
    return kept or candidates


def choose_release(releases: list[dict], first_release_date: str | None) -> dict | None:
    candidates = [r for r in releases if _track_count(r) > 0]
    if not candidates:
        return None
    candidates = _narrow(candidates, lambda r: r.get("status") == "Official")
    candidates = _narrow(
        candidates,
        lambda r: (
            not has_edition_marker(r.get("disambiguation"))
            and not has_edition_marker(r.get("title"))
        ),
    )
    first = _months(first_release_date)
    if first is not None:
        candidates = _narrow(
            candidates,
            lambda r: (m := _months(r.get("date"))) is not None and m - first <= 12,
        )
    return min(
        candidates,
        key=lambda r: (_track_count(r), _months(r.get("date")) or 10**9, r["id"]),
    )


def tracklist(release: dict) -> tuple[Track, ...]:
    tracks: list[Track] = []
    for medium in _audio_media(release):
        for t in medium.get("tracks") or []:
            recording = t.get("recording") or {}
            title = t.get("title") or recording.get("title") or ""
            tracks.append(
                Track(
                    position=len(tracks) + 1,
                    title=title,
                    norm_title=title_key(title),
                    recording_mbid=recording.get("id"),
                    length_ms=t.get("length") or recording.get("length"),
                )
            )
    return tuple(tracks)


def resolve_group(releases: list[dict]) -> Resolution | None:
    """Turn a browse of one release group into its metadata and canonical tracklist."""
    group = next((r["release-group"] for r in releases if r.get("release-group")), {})
    release = choose_release(releases, group.get("first-release-date"))
    if release is None:
        return None
    credit = (group.get("artist-credit") or release.get("artist-credit") or [{}])[0]
    artist = credit.get("artist") or {}
    title = group.get("title") or release.get("title") or ""
    first = group.get("first-release-date") or ""
    return Resolution(
        title=title,
        norm_key=album_key(title) or title_key(title) or "untitled",
        primary_type=group.get("primary-type"),
        secondary_types=tuple(group.get("secondary-types") or ()),
        first_release_year=int(first[:4]) if first[:4].isdigit() else None,
        artist_mbid=artist.get("id"),
        artist_name=credit.get("name") or artist.get("name"),
        release_mbid=release["id"],
        tracks=tracklist(release),
    )
