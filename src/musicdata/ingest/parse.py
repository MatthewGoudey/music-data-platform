"""ListenBrainz listen JSON → ParsedListen. Pure functions, no I/O.

A listen is mapped when ListenBrainz attached an `mbid_mapping` (about 88% of them):
then the artist credit carries a MusicBrainz ID and the release group is known. An
unmapped listen has only raw strings; its artist is the part before any "feat."
(ADR 0015: a listen belongs to its first credited artist).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from musicdata.identity import album_key, norm_key, split_featured, title_key


@dataclass(frozen=True)
class Credit:
    name: str
    mbid: str | None
    key: str


@dataclass(frozen=True)
class ParsedListen:
    listened_at: datetime
    track_name: str
    norm_title: str
    artist_name: str
    artist: Credit
    release_name: str | None
    release_key: str
    release_artist_name: str | None
    release_group_mbid: str | None
    release_mbid: str | None
    recording_mbid: str | None
    recording_msid: str | None
    duration_ms: int | None
    client: str | None
    inserted_at: datetime | None


def _ts(value: object) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(int(value), tz=UTC)


def _str(value: object) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _duration(info: dict) -> int | None:
    if info.get("duration_ms") is not None:
        return int(info["duration_ms"])
    if info.get("duration") is not None:
        return int(float(info["duration"]) * 1000)
    return None


def parse_listen(raw: dict) -> ParsedListen | None:
    """Return None for a listen that cannot carry identity (no artist or track name)."""
    meta = raw.get("track_metadata") or {}
    info = meta.get("additional_info") or {}
    mapping = meta.get("mbid_mapping") or {}

    artist_name = _str(meta.get("artist_name"))
    track_name = _str(meta.get("track_name"))
    if not artist_name or not track_name or raw.get("listened_at") is None:
        return None

    artist: Credit | None = None
    for a in mapping.get("artists") or []:
        name = _str(a.get("artist_credit_name"))
        if name and norm_key(name):
            artist = Credit(name=name, mbid=_str(a.get("artist_mbid")), key=norm_key(name))
            break
    if artist is None:
        primary, _featured = split_featured(artist_name)
        if not norm_key(primary):
            return None
        artist = Credit(name=primary, mbid=None, key=norm_key(primary))

    release_name = _str(meta.get("release_name"))
    return ParsedListen(
        listened_at=_ts(raw["listened_at"]),  # type: ignore[arg-type]
        track_name=track_name,
        norm_title=title_key(track_name),
        artist_name=artist_name,
        artist=artist,
        release_name=release_name,
        release_key=album_key(release_name) if release_name else "",
        release_artist_name=_str(info.get("release_artist_name")),
        release_group_mbid=_str(mapping.get("release_group_mbid")),
        release_mbid=_str(mapping.get("release_mbid")),
        recording_mbid=_str(mapping.get("recording_mbid")),
        recording_msid=_str(raw.get("recording_msid")),
        duration_ms=_duration(info),
        client=_str(info.get("music_service") or info.get("media_player"))
        or _str(info.get("submission_client")),
        inserted_at=_ts(raw.get("inserted_at")),
    )
