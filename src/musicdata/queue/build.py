"""Building a queue (docs/QUEUE_SPEC.md section 8). Pure: candidates and pools in, items out.

1. Pinned albums, oldest pin first; they ignore filters and the artist rule.
2. Revisit slots, round(n × revisit share), round-robin over spaced → abandoned →
   unfinished, most overdue first; slots a pool cannot fill pass on, then to new.
3. Thread slots (companion spec 5.3, queue spec v17): one per finished album in turn, newest
   first, each taking the best-ranked candidate its thread reaches; with shuffle, the finished
   albums in random order and a draw weighted toward the stronger connections. Slots no thread
   fills go to new.
4. One wildcard (when nothing is pinned), drawn from ranks 51–500 of the scored list.
5. New slots: the best scores; with shuffle, a weighted draw (weight = score) from the
   top SHUFFLE_POOL, leaving out `exclude`.
6. One album per artist across the queue, pins exempt.
7. The same profile, Chicago day and data give the same queue: every draw uses `seed`.

The queue reads pins, new, revisits, thread, wildcard, in that order.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Collection, Mapping, Sequence
from datetime import date

from musicdata.queue import config
from musicdata.queue.score import Item


def day_seed(profile: str, day: date) -> int:
    """The default seed: stable for one profile on one Chicago calendar day."""
    digest = hashlib.sha256(f"{profile}:{day.isoformat()}".encode()).hexdigest()
    return int(digest[:12], 16)


class _Queue:
    def __init__(self) -> None:
        self.items: list[Item] = []
        self.groups: set[int] = set()
        self.artists: set[int] = set()

    def fits(self, item: Item) -> bool:
        return item.release_group_id not in self.groups and item.artist_id not in self.artists

    def add(self, item: Item, slot: str, *, artist_rule: bool = True) -> None:
        item.slot = slot
        self.items.append(item)
        self.groups.add(item.release_group_id)
        if artist_rule:
            self.artists.add(item.artist_id)


def _revisits(q: _Queue, pools: Mapping[str, Sequence[Item]], slots: int) -> list[Item]:
    queues = {name: [i for i in pools.get(name, ()) if q.fits(i)] for name in config.POOLS}
    picked: list[Item] = []
    while len(picked) < slots and any(queues.values()):
        for name in config.POOLS:
            if len(picked) == slots:
                break
            while queues[name]:
                item = queues[name].pop(0)
                if q.fits(item) and all(item.artist_id != p.artist_id for p in picked):
                    picked.append(item)
                    q.groups.add(item.release_group_id)
                    q.artists.add(item.artist_id)
                    break
    return picked


def _weighted(pool: list[Item], k: int, q: _Queue, rng: random.Random) -> list[Item]:
    left = [i for i in pool if q.fits(i)]
    out: list[Item] = []
    while len(out) < k and left:
        total = sum(max(i.score or 0.0, 1e-9) for i in left)
        r = rng.random() * total
        idx = 0
        while idx < len(left) - 1:
            r -= max(left[idx].score or 0.0, 1e-9)
            if r <= 0:
                break
            idx += 1
        item = left.pop(idx)
        out.append(item)
        q.groups.add(item.release_group_id)
        q.artists.add(item.artist_id)
        left = [i for i in left if q.fits(i)]
    return out


def _threads(
    q: _Queue,
    threads: Sequence[tuple[Item, str, int]],
    slots: int,
    skip: Collection[int],
    rng: random.Random | None,
) -> list[Item]:
    """Thread slots: the finished albums take turns (newest first, or in random order with
    shuffle), each giving its best-ranked fitting candidate, or a draw weighted toward its
    stronger connections with shuffle."""
    groups: dict[int, list[tuple[Item, str]]] = {}
    for item, because, source in threads:  # already in rank order within each finished album
        groups.setdefault(source, []).append((item, because))
    order = list(groups)
    if rng is not None:
        rng.shuffle(order)
    picked: list[Item] = []
    while len(picked) < slots and order:
        for source in list(order):
            if len(picked) == slots:
                break
            left = [(i, b) for i, b in groups[source]
                    if q.fits(i) and i.release_group_id not in skip]  # fmt: skip
            if not left:
                order.remove(source)
                continue
            if rng is None:
                item, because = left[0]
            else:
                weights = [len(left) - k for k in range(len(left))]
                item, because = rng.choices(left, weights=weights)[0]
            item.because = because
            picked.append(item)
            q.groups.add(item.release_group_id)
            q.artists.add(item.artist_id)
    return picked


def build(
    candidates: Sequence[Item],
    pools: Mapping[str, Sequence[Item]],
    pins: Sequence[Item],
    *,
    n: int,
    revisit_share: float,
    wildcard: int,
    seed: int,
    shuffle: bool = False,
    exclude: Collection[int] = (),
    thread: int = 0,
    threads: Sequence[tuple[Item, str, int]] = (),
) -> list[Item]:
    rng = random.Random(seed)
    q = _Queue()
    for item in pins[:n]:
        q.add(item, "pinned", artist_rule=False)  # pins never block another album
    open_slots = n - len(q.items)

    skip = set(exclude) if shuffle else set()
    if shuffle:
        # Shuffle re-draws every slot but the pins: each pool in random order (not most
        # overdue first), leaving out what this round of shuffles already showed.
        pools = {
            name: rng.sample(
                [i for i in items if i.release_group_id not in skip],
                len([i for i in items if i.release_group_id not in skip]),
            )
            for name, items in pools.items()
        }
    revisits = _revisits(q, pools, min(round(n * revisit_share), open_slots))
    open_slots -= len(revisits)

    threaded = _threads(q, threads, min(thread, open_slots), skip, rng if shuffle else None)
    open_slots -= len(threaded)

    wild: list[Item] = []
    if wildcard and not pins and open_slots > 0:
        lo, hi = config.WILDCARD_RANKS
        deep = [i for i in candidates[lo - 1 : hi] if q.fits(i) and i.release_group_id not in skip]
        for _ in range(min(wildcard, open_slots)):
            deep = [i for i in deep if q.fits(i)]
            if not deep:
                break
            item = deep[rng.randrange(len(deep))]
            wild.append(item)
            q.groups.add(item.release_group_id)
            q.artists.add(item.artist_id)
        open_slots -= len(wild)

    fresh: list[Item] = []
    if shuffle:
        pool = [i for i in candidates[: config.SHUFFLE_POOL] if i.release_group_id not in skip]
        fresh = _weighted(pool, open_slots, q, rng)
    for item in candidates:  # in rank order: all of them without shuffle, the rest with it
        if len(fresh) >= open_slots:
            break
        if q.fits(item) and not (shuffle and item.release_group_id in exclude):
            fresh.append(item)
            q.groups.add(item.release_group_id)
            q.artists.add(item.artist_id)

    for item in fresh:
        q.add(item, "new")
    for item in revisits:
        q.add(item, "revisit")
    for item in threaded:
        q.add(item, "thread")
    for item in wild:
        q.add(item, "wildcard")
    return q.items
