"""Seed files → plain records. Pure functions, no database (docs/QUEUE_SPEC.md section 5).

`seeds/lists/_lists.csv` registers each list and names its file. Published lists share
one shape (`position, artist, album, year, priority, genre, descriptors, note`); the atlas
(`seeds/atlas/albums.csv`) carries its own columns and maps onto the same entry: lane from
the leading token of `primary_lane`, zone, layer, priority, start here, and every other
column (plus the prose in `album_notes.csv`) into `facets`.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from musicdata.identity import album_key, norm_key, title_key

PRIORITIES = ("Essential", "Recommended", "Deep cut")
SHARED_COLUMNS = ("position", "artist", "album", "year", "priority", "genre", "descriptors", "note")
ATLAS_ENTRY_COLUMNS = {
    "atlas_id",
    "artist",
    "album",
    "year",
    "layer",
    "zone",
    "priority",
    "start_here",
}


@dataclass(frozen=True)
class ListSpec:
    slug: str
    name: str
    goal: str
    weight: float
    ranked: bool
    default_priority: str | None
    file: str
    source: str | None


@dataclass
class Entry:
    raw_artist: str
    raw_album: str
    artist_key: str
    album_key: str
    position: int | None = None
    year: int | None = None
    priority: str | None = None
    lane_id: str | None = None
    zone: str | None = None
    layer: str | None = None
    start_here: bool = False
    facets: dict[str, object] = field(default_factory=dict)
    note: str | None = None


@dataclass
class ListFile:
    spec: ListSpec
    entries: list[Entry]
    rows: int  # data rows in the file
    duplicates: int  # rows that repeat an earlier row's keys
    keyless: int  # rows whose artist or album gives an empty key


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]


def _int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def read_registry(path: Path) -> list[ListSpec]:
    specs = []
    for r in _rows(path):
        specs.append(
            ListSpec(
                slug=r["slug"],
                name=r["name"],
                goal=r["goal"],
                weight=float(r["weight"] or 1.0),
                ranked=r["ranked"].lower() == "true",
                default_priority=r["default_priority"] or None,
                file=r["file"],
                source=r["source"] or None,
            )
        )
    return specs


def _keys(artist: str, album: str) -> tuple[str, str]:
    return norm_key(artist), album_key(album) or title_key(album)


def _shared_entry(r: dict[str, str]) -> Entry:
    a_key, b_key = _keys(r["artist"], r["album"])
    facets = {k: r[k] for k in ("genre", "descriptors") if r.get(k)}
    return Entry(
        raw_artist=r["artist"],
        raw_album=r["album"],
        artist_key=a_key,
        album_key=b_key,
        position=_int(r.get("position", "")),
        year=_int(r.get("year", "")),
        priority=r.get("priority") if r.get("priority") in PRIORITIES else None,
        facets=facets,
        note=r.get("note") or None,
    )


def _atlas_entry(r: dict[str, str], notes: dict[str, dict[str, str]]) -> Entry:
    a_key, b_key = _keys(r["artist"], r["album"])
    lane = r.get("primary_lane", "").split(" ", 1)[0] or None
    facets: dict[str, object] = {k: v for k, v in r.items() if k not in ATLAS_ENTRY_COLUMNS and v}
    facets["atlas_id"] = r["atlas_id"]
    for k, v in notes.get(r["atlas_id"], {}).items():
        if k != "atlas_id" and v:
            facets[k] = v
    return Entry(
        raw_artist=r["artist"],
        raw_album=r["album"],
        artist_key=a_key,
        album_key=b_key,
        year=_int(r.get("year", "")),
        priority=r.get("priority") if r.get("priority") in PRIORITIES else None,
        lane_id=lane,
        zone=r.get("zone") or None,
        layer=r.get("layer") or None,
        start_here=r.get("start_here", "").upper() == "Y",
        facets=facets,
    )


YEAR_MARK = " #"


def base_album_key(key: str) -> str:
    """The identity key of an entry's album, without a year mark (`weezer #2001` → `weezer`)."""
    return key.split(YEAR_MARK, 1)[0]


def _far_apart(a: int | None, b: int | None) -> bool:
    return a is not None and b is not None and abs(a - b) > 1


def read_list(spec: ListSpec, root: Path) -> ListFile:
    """Every entry of one list, deduplicated on its keys (first row wins).

    Two rows with the same keys whose years are more than one apart are two albums
    (Weezer 1994 and 2001, both titled "Weezer"): the later row keeps its own entry, its
    album key marked with its year (`weezer #2001`). Resolve matches on the unmarked key.
    """
    path = root / spec.file
    rows = _rows(path)
    if spec.slug == "v_atlas":
        notes_path = path.with_name("album_notes.csv")
        notes = {r["atlas_id"]: r for r in _rows(notes_path)} if notes_path.exists() else {}
        make = lambda r: _atlas_entry(r, notes)  # noqa: E731
    else:
        missing = [c for c in SHARED_COLUMNS if rows and c not in rows[0]]
        if missing:
            raise ValueError(f"{path}: missing columns {missing}")
        make = _shared_entry
    entries: dict[tuple[str, str], Entry] = {}
    by_key: dict[tuple[str, str], list[Entry]] = {}  # kept entries per unmarked key
    duplicates = keyless = 0
    for r in rows:
        e = make(r)
        if not e.artist_key or not e.album_key:
            keyless += 1
            continue
        same = by_key.setdefault((e.artist_key, e.album_key), [])
        if same:
            if not all(_far_apart(x.year, e.year) for x in same):
                duplicates += 1
                continue
            e.album_key = f"{e.album_key}{YEAR_MARK}{e.year}"
        same.append(e)
        entries[(e.artist_key, e.album_key)] = e
    return ListFile(spec, list(entries.values()), len(rows), duplicates, keyless)


@dataclass(frozen=True)
class Lane:
    lane_id: str
    name: str
    zone: str | None
    era_span: str | None
    definition: str | None
    parents: tuple[str, ...]


def read_lanes(path: Path) -> list[Lane]:
    return [
        Lane(
            lane_id=r["lane_id"],
            name=r["name"],
            zone=r.get("zone") or None,
            era_span=r.get("era_span") or None,
            definition=r.get("definition") or None,
            parents=tuple(p.strip() for p in r.get("parent_lanes", "").split(",") if p.strip()),
        )
        for r in _rows(path)
        if r.get("lane_id")
    ]


def read_paths(path: Path) -> list[tuple[str, int, str, str | None]]:
    return [
        (r["path"], int(r["step"]), r["atlas_id"], r.get("connection_to_next") or None)
        for r in _rows(path)
        if r.get("path") and r.get("step")
    ]
