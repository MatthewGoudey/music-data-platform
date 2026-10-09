from __future__ import annotations

from musicdata.api.routers.queue import history_line


def test_a_finished_album_says_how_often() -> None:
    assert history_line(1, 1.0, 10, 10) == "Finished once · every track heard"
    assert history_line(3, 0.9, 9, 10) == "Finished 3× · 9 of 10 tracks ever heard"


def test_an_unfinished_album_shows_its_best_and_what_was_heard() -> None:
    assert history_line(0, 0.6, 7, 10) == "Not finished yet · best 60% · 7 of 10 tracks ever heard"
    assert history_line(0, 0.4, 10, 10) == "Not finished yet · best 40% · every track heard"


def test_missing_stats_still_read_cleanly() -> None:
    assert history_line(None, None, None, None) == "Not finished yet · best 0%"
