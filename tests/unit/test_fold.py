"""Folding listens off groups that can never get a tracklist (derive/fold.py)."""

from __future__ import annotations

from musicdata.derive.fold import pairs


def _g(rg: int, key: str, akey: str, name: str = "") -> dict:
    return {"release_group_id": rg, "norm_key": key, "akey": akey, "name": name or akey}


def test_ram_folds_from_both_stuck_groups_into_the_real_album() -> None:
    stuck = [
        _g(962, "ram", "paul mccartney", "Paul McCartney"),
        _g(553, "ram", "paul mccartney linda mccartney", "Paul McCartney, Linda McCartney"),
    ]
    homes = [_g(22776, "ram", "paul mccartney"), _g(1, "mccartney", "paul mccartney")]
    assert sorted(pairs(stuck, homes)) == [(553, 22776), (962, 22776)]


def test_two_possible_homes_or_none_leave_the_group_alone() -> None:
    stuck = [_g(10, "weezer", "weezer", "Weezer"), _g(11, "lost", "nobody", "Nobody")]
    homes = [_g(20, "weezer", "weezer"), _g(21, "weezer", "weezer")]
    assert pairs(stuck, homes) == []
