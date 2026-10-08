from __future__ import annotations

import copy
import json
from pathlib import Path

from musicdata.identity import has_edition_marker
from musicdata.resolve.canonical import choose_release, resolve_group, tracklist

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "mb_agaetis_byrjun.json").read_text("utf-8")
)
RELEASES = FIXTURE["releases"]


def _release(rid: str, tracks: int, *, date="2000-01", status="Official", dis="", fmt="CD"):
    media = [{"format": fmt, "tracks": [{"title": f"T{i}"} for i in range(tracks)]}]
    return {"id": rid, "date": date, "status": status, "disambiguation": dis, "media": media}


def test_the_real_group_picks_the_original_icelandic_cd() -> None:
    r = resolve_group(RELEASES)
    assert r is not None
    assert r.release_mbid.startswith("743f2ede")
    assert r.title == "Ágætis byrjun"
    assert r.primary_type == "Album"
    assert r.first_release_year == 1999
    assert r.artist_mbid == "f6f2326f-6b25-4170-b89d-e235b25508e8"
    assert len(r.tracks) == 10
    assert r.tracks[2].norm_title == "staralfur"
    assert not r.is_compilation and not r.is_box_set


def test_deluxe_editions_lose_even_when_earliest() -> None:
    releases = [_release("deluxe", 24, date="1999-06", dis="20th anniversary deluxe edition")]
    releases.append(_release("standard", 10, date="1999-07"))
    assert choose_release(releases, "1999-06")["id"] == "standard"


def test_releases_outside_twelve_months_lose_to_one_inside() -> None:
    releases = [_release("vinyl-2000", 9, date="2000-08"), _release("cd-1999", 10, date="1999-06")]
    assert choose_release(releases, "1999-06")["id"] == "cd-1999"


def test_fewest_tracks_wins_within_the_window() -> None:
    releases = [_release("jp-bonus", 12, date="1999-09"), _release("uk", 10, date="1999-10")]
    assert choose_release(releases, "1999-06")["id"] == "uk"


def test_filters_that_would_empty_the_set_are_skipped() -> None:
    releases = [_release("boot", 8, status="Bootleg", date="")]
    assert choose_release(releases, "1999-06")["id"] == "boot"


def test_video_media_do_not_count_as_tracks() -> None:
    r = _release("cd-dvd", 10)
    r["media"].append({"format": "DVD-Video", "tracks": [{"title": "Film"}] * 5})
    assert len(tracklist(r)) == 10


def test_releases_without_tracks_are_ignored() -> None:
    empty = _release("empty", 0)
    assert choose_release([empty], None) is None


def test_box_sets_are_flagged() -> None:
    releases = copy.deepcopy(RELEASES[:1])
    releases[0]["media"] = [{"format": "CD", "tracks": [{"title": f"T{i}"} for i in range(31)]}]
    r = resolve_group(releases)
    assert r is not None and r.is_box_set


def test_edition_markers() -> None:
    assert has_edition_marker("20th anniversary deluxe edition")
    assert has_edition_marker("Remastered")
    assert not has_edition_marker("Bandcamp")
    assert not has_edition_marker("")
    assert not has_edition_marker(None)
