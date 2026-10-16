from collections import Counter

from musicdata.ingest.reconcile import Stored, compare


def test_extra_listen_is_the_title_listenbrainz_lacks():
    upstream = {100: Counter({"a": 1}), 200: Counter({"b": 1})}
    stored = [Stored(1, 100, "a"), Stored(2, 100, "deleted"), Stored(3, 200, "b")]
    diff = compare(upstream, stored)
    assert diff.extra == [2]
    assert diff.ambiguous == []
    assert diff.missing == 0


def test_second_gone_upstream_is_extra():
    diff = compare({}, [Stored(7, 300, "x")])
    assert diff.extra == [7]


def test_repeat_in_one_second_is_ambiguous_not_extra():
    upstream = {100: Counter({"a": 1})}
    stored = [Stored(1, 100, "a"), Stored(2, 100, "a")]
    diff = compare(upstream, stored)
    assert diff.extra == []
    assert diff.ambiguous == [1, 2]


def test_missing_counts_listens_not_stored():
    diff = compare({100: Counter({"a": 2}), 200: Counter({"b": 1})}, [Stored(1, 100, "a")])
    assert diff.missing == 2
    assert diff.extra == []
