from __future__ import annotations

from datetime import UTC, datetime, timedelta

from musicdata.derive.sessions import Play, TrackRef, detect_sessions, eligible

T0 = datetime(2026, 10, 7, 21, 0, tzinfo=UTC)
TRACKS = [TrackRef(i, f"rec-{i}", f"track {i}") for i in range(1, 11)]  # a 10-track album


def _plays(positions: list[int], *, start: datetime = T0, every: int = 4, mbid: bool = True):
    return [
        Play(start + timedelta(minutes=every * n), f"rec-{p}" if mbid else None, f"track {p}")
        for n, p in enumerate(positions)
    ]


def test_a_whole_album_is_one_full_session() -> None:
    [s] = detect_sessions(_plays(list(range(1, 11))), TRACKS)
    assert (s.session_type, s.tracks_played, s.completion, s.listen_count) == ("full", 10, 1.0, 10)


def test_eight_of_ten_is_full_seven_is_partial() -> None:
    assert detect_sessions(_plays(list(range(1, 9))), TRACKS)[0].session_type == "full"
    assert detect_sessions(_plays(list(range(1, 8))), TRACKS)[0].session_type == "partial"


def test_partial_needs_three_tracks_and_a_quarter() -> None:
    assert detect_sessions(_plays([1, 2]), TRACKS) == []
    assert detect_sessions(_plays([1, 2, 3]), TRACKS)[0].session_type == "partial"
    short = TRACKS[:4]
    assert detect_sessions(_plays([1, 2]), short) == []  # 0.5 but only 2 tracks


def test_a_gap_over_thirty_minutes_splits_sessions() -> None:
    first = _plays(list(range(1, 11)))
    second = _plays(list(range(1, 11)), start=first[-1].listened_at + timedelta(minutes=31))
    assert len(detect_sessions(first + second, TRACKS)) == 2
    joined = _plays(list(range(1, 11)), start=first[-1].listened_at + timedelta(minutes=30))
    assert len(detect_sessions(first + joined, TRACKS)) == 1


def test_repeats_and_bonus_tracks_never_overshoot() -> None:
    plays = _plays([1, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])  # 11 and 12 are bonus
    [s] = detect_sessions(plays, TRACKS)
    assert s.tracks_played == 10 and s.completion == 1.0 and s.listen_count == 14


def test_unmapped_listens_match_by_title() -> None:
    [s] = detect_sessions(_plays(list(range(1, 11)), mbid=False), TRACKS)
    assert s.session_type == "full"


def test_a_remaster_recording_still_matches_by_title() -> None:
    plays = [
        Play(T0 + timedelta(minutes=4 * p), f"remaster-{p}", f"track {p}") for p in range(1, 11)
    ]
    assert detect_sessions(plays, TRACKS)[0].tracks_played == 10


def test_eligibility() -> None:
    assert eligible("Album", False, False, 10)
    assert eligible("EP", False, False, 4)
    assert not eligible("Single", False, False, 2)
    assert not eligible("Album", True, False, 10)
    assert not eligible("Album", False, True, 40)
    assert not eligible("Album", False, False, None)
    assert not eligible(None, False, False, 10)


def test_live_takes_and_roman_parts_match_the_tracklist() -> None:
    folsom = [
        TrackRef(1, "lp-1", "folsom prison blues", "Folsom Prison Blues"),
        TrackRef(2, "lp-2", "dark as dungeon", "Dark as Dungeon"),
        TrackRef(3, "lp-3", "cocaine blues", "Cocaine Blues"),
    ]
    live = " (Live at Folsom State Prison, Folsom, CA (1st Show) - January 1968)"
    plays = [
        Play(T0 + timedelta(minutes=4 * n), f"legacy-{n}", "", title + live)
        for n, title in enumerate(["Folsom Prison Blues", "Dark as Dungeon", "Cocaine Blues"])
    ]
    [s] = detect_sessions(plays, folsom)
    assert (s.session_type, s.tracks_played) == ("full", 3)


def test_a_joined_track_answers_to_either_half() -> None:
    supreme = [
        TrackRef(
            1,
            None,
            "a love supreme part 1 acknowledgement",
            "A Love Supreme, Part 1: Acknowledgement",
        ),
        TrackRef(2, None, "a love supreme part 2 resolution", "A Love Supreme, Part 2: Resolution"),
        TrackRef(3, None, "x", "A Love Supreme, Part 3: Pursuance / A Love Supreme, Part 4: Psalm"),
    ]
    names = [
        "A Love Supreme, Pt. I - Acknowledgement",
        "A Love Supreme, Pt. II - Resolution",
        "A Love Supreme, Pt. III - Pursuance",
        "A Love Supreme, Pt. IV - Psalm",
    ]
    plays = [Play(T0 + timedelta(minutes=8 * n), None, "", name) for n, name in enumerate(names)]
    [s] = detect_sessions(plays, supreme)
    assert (s.session_type, s.tracks_played, s.completion) == ("full", 3, 1.0)
