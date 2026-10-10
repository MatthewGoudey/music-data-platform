"""The queue's pure parts: composition, one per artist, determinism, shuffle, pools, scores
(docs/QUEUE_SPEC.md sections 7–9; tests Q1, Q4 and Q5 on fixtures)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from musicdata.queue.build import build, day_seed
from musicdata.queue.pools import due
from musicdata.queue.score import Item, Profile, passes, position_factor, why_line

NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)


def _item(rg: int, artist: int | None = None, score: float = 1.0, **kw) -> Item:
    return Item(
        release_group_id=rg,
        artist_id=artist if artist is not None else rg,
        artist=f"A{artist if artist is not None else rg}",
        album=f"B{rg}",
        year=2000,
        primary_type="Album",
        status=kw.pop("status", "unheard"),
        score=score,
        **kw,
    )


def _cands(count: int = 600) -> list[Item]:
    return [_item(i, score=1000 - i) for i in range(1, count + 1)]


def _pools() -> dict[str, list[Item]]:
    return {
        "spaced": [_item(9001, pool="spaced", status="heard"), _item(9002, pool="spaced")],
        "unfinished": [_item(9003, pool="unfinished", status="started")],
    }


def _build(**kw):
    args = dict(n=10, revisit_share=0.2, wildcard=1, seed=day_seed("default", date(2026, 10, 9)))
    args.update(kw)
    return build(
        args.pop("cands", _cands()), args.pop("pools", _pools()), args.pop("pins", []), **args
    )


def test_q1_composition() -> None:
    items = _build()
    slots = [i.slot for i in items]
    assert len(items) == 10
    assert slots.count("revisit") == 2 and slots.count("wildcard") == 1
    assert slots.count("new") == 7
    assert [i.release_group_id for i in items if i.slot == "new"] == list(range(1, 8))
    wild = next(i for i in items if i.slot == "wildcard")
    assert 51 <= wild.release_group_id <= 500


def test_q1_pins_first_and_no_wildcard_with_pins() -> None:
    pins = [_item(7000, artist=1), _item(7001, artist=1)]
    items = _build(pins=pins)
    assert [i.slot for i in items[:2]] == ["pinned", "pinned"]
    assert "wildcard" not in [i.slot for i in items]
    assert len(items) == 10
    # pins are exempt from the artist rule: artist 1's own new album still shows
    assert any(i.release_group_id == 1 for i in items)


def test_q1_revisit_slots_pass_to_new_when_pools_are_empty() -> None:
    items = _build(pools={})
    assert [i.slot for i in items].count("new") == 9


def test_q4_one_album_per_artist() -> None:
    cands = [_item(i, artist=i // 2, score=1000 - i) for i in range(1, 600)]
    items = _build(cands=cands, pools={})
    artists = [i.artist_id for i in items]
    assert len(artists) == len(set(artists))


def test_q5_same_day_same_queue_and_same_seed_same_shuffle() -> None:
    assert [i.release_group_id for i in _build()] == [i.release_group_id for i in _build()]
    a = _build(shuffle=True, seed=42)
    b = _build(shuffle=True, seed=42)
    assert [i.release_group_id for i in a] == [i.release_group_id for i in b]
    c = _build(shuffle=True, seed=43)
    assert [i.release_group_id for i in a] != [i.release_group_id for i in c]


def test_shuffle_draws_from_the_top_200_and_leaves_out_exclude() -> None:
    first = _build(shuffle=True, seed=1, pools={})
    new = {i.release_group_id for i in first if i.slot == "new"}
    assert all(rg <= 200 for rg in new)
    second = _build(shuffle=True, seed=2, pools={}, exclude=new)
    assert not new & {i.release_group_id for i in second if i.slot == "new"}


def test_shuffle_fills_from_the_ranked_list_when_exclude_empties_the_pool() -> None:
    items = _build(shuffle=True, seed=3, pools={}, exclude=set(range(1, 201)))
    assert len([i for i in items if i.slot == "new"]) == 9


def _history(**kw) -> dict:
    row = dict(
        full_sessions=0,
        partial_sessions=0,
        listens=0,
        last_session_at=None,
        last_full_at=None,
        last_listened_at=None,
        best_completion=None,
        on_list=True,
    )
    row.update(kw)
    return row


def test_spaced_is_due_after_the_interval_and_a_month_of_rest() -> None:
    ago = NOW - timedelta(days=61)
    d = due(
        _history(full_sessions=2, last_full_at=ago, last_session_at=ago, last_listened_at=ago), NOW
    )
    assert d.pool == "spaced" and d.reason.startswith("3rd listen due, last full play 61 days")
    soon = NOW - timedelta(days=20)
    assert due(_history(full_sessions=1, last_full_at=soon, last_session_at=soon), NOW) is None


def test_abandoned_and_unfinished() -> None:
    long_ago = NOW - timedelta(days=400)
    d = due(
        _history(
            full_sessions=5,
            listens=80,
            last_full_at=long_ago,
            last_session_at=long_ago,
            last_listened_at=long_ago,
        ),
        NOW,
    )
    assert d.pool == "abandoned"
    tried = NOW - timedelta(days=20)
    d = due(_history(partial_sessions=1, best_completion=0.4, last_session_at=tried), NOW)
    assert d.pool == "unfinished" and d.reason.startswith("best run 40%")
    assert due(_history(partial_sessions=1, on_list=False, last_session_at=tried), NOW) is None


def test_position_factor_and_filters() -> None:
    assert position_factor(1, 500, True) == 1.0 and position_factor(500, 500, True) == 0.5
    assert position_factor(250, 500, False) == 1.0
    e = {
        "slug": "v_atlas",
        "goal": "depth",
        "zone": "Core",
        "layer": "L1",
        "lane_id": "C1",
        "primary_type": "Album",
        "year": 1994,
        "first_release_year": None,
        "descriptors": "warm, twangy",
    }
    assert passes(e, Profile("x").filters, ())
    assert passes(e, {"lists": ["v_atlas"], "decades": [1990]}, ())
    assert not passes(e, {"goals": ["canon"]}, ())
    assert passes(e, {"descriptors_any": ["Twangy"]}, ())
    assert not passes(e, {"tags": ["rainy"]}, ["study"])


def test_why_line_reads_like_the_spec() -> None:
    atlas = {
        "list": "v_atlas",
        "label": "V Atlas",
        "position": None,
        "priority": "Essential",
        "lane": "C1",
        "lane_name": "Indie twang",
        "zone": "Core",
        "start_here": True,
    }
    rs = {
        "list": "rolling_stone_500",
        "label": "Rolling Stone",
        "position": 2,
        "priority": None,
        "lane": None,
        "lane_name": None,
        "zone": None,
        "start_here": False,
    }
    assert why_line([atlas]) == "V Atlas · C1 Indie twang · Essential · start here"
    assert why_line([rs]) == "Rolling Stone #2"
    lists = [{**rs, "label": f"List {n}", "position": n} for n in range(1, 7)]
    assert why_line(lists) == " · ".join(f"List {n} #{n}" for n in range(1, 7))  # no "+3 more"


def test_shuffle_redraws_revisits_and_the_wildcard_too() -> None:
    pools = {"spaced": [_item(9000 + i, pool="spaced", status="heard") for i in range(12)]}
    seen = set()
    for seed in range(1, 4):
        items = _build(shuffle=True, seed=seed, pools=pools, exclude=seen)
        shown = {i.release_group_id for i in items if i.slot != "pinned"}
        assert not shown & seen  # nothing repeats across presses
        seen |= shown
    ranked = _build(pools=pools)
    assert [i.release_group_id for i in ranked if i.slot == "revisit"] == [9000, 9001]


def _threads(cands: list[Item], spec: dict[int, list[int]]) -> list[tuple[Item, str, int]]:
    """Thread choices: finished album → its targets in rank order."""
    by_rg = {i.release_group_id: i for i in cands}
    return [(by_rg[t], f"Because you finished F{src}: c{t}", src)
            for src, targets in spec.items() for t in targets]  # fmt: skip


def test_thread_slots_take_turns_across_finished_albums() -> None:
    cands = _cands()
    threads = _threads(cands, {1001: [300, 301, 302], 1002: [310, 311], 1003: [320]})
    items = _build(cands=cands, thread=3, threads=threads)
    thread = [i for i in items if i.slot == "thread"]
    assert [i.release_group_id for i in thread] == [300, 310, 320]  # best of each, newest first
    assert thread[0].because == "Because you finished F1001: c300"
    slots = [i.slot for i in items]
    assert len(items) == 10 and slots.count("new") == 4  # 2 revisits, 3 threads, 1 wildcard
    assert slots.index("thread") < slots.index("wildcard")


def test_thread_slots_go_round_again_when_few_albums_finished() -> None:
    cands = _cands()
    items = _build(cands=cands, thread=3, threads=_threads(cands, {1001: [300, 301, 302]}))
    assert [i.release_group_id for i in items if i.slot == "thread"] == [300, 301, 302]


def test_shuffle_redraws_threads_and_skips_what_was_shown() -> None:
    cands = _cands()
    spec = {
        src: list(range(200 + 20 * k, 220 + 20 * k)) for k, src in enumerate((1001, 1002, 1003))
    }
    seen: set[int] = set()
    for seed in range(8):
        items = _build(cands=cands, thread=3, threads=_threads(cands, spec), shuffle=True,
                       seed=seed, exclude=frozenset(seen))  # fmt: skip
        drawn = [i.release_group_id for i in items if i.slot == "thread"]
        assert len(drawn) == 3 and not set(drawn) & seen
        seen |= set(drawn)
    assert len(seen) == 24  # every shuffle drew fresh threads


def test_thread_slot_falls_back_to_new() -> None:
    items = _build(thread=1, threads=[])
    assert [i.slot for i in items].count("new") == 7


def test_graph_affinity_is_bounded() -> None:
    from musicdata.queue.score import graph_affinity

    assert graph_affinity(None, 0.3) == 1.0
    assert graph_affinity(1.5, 0.3) == 1.15
    assert graph_affinity(99, 0.3) == 1.3  # never more than ×(1 + graph_weight)
    assert graph_affinity(99, 0.0) == 1.0  # profiles without the graph score as before
