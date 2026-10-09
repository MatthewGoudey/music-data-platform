"""Weekly Billboard 200 charts → a ranked list of the albums Matt's peers would know.

    uv run python scripts/convert_billboard.py seeds/lists/billboard200.csv seeds/lists/billboard_200.csv

The download is one row per album per chart week since 1963 (Date, Song = the album,
Artist, Rank, ...). Albums are scored by staying power in Matt's listening lifetime:

    score = weeks charted since 2005 + 0.3 × weeks before + 2 × weeks in the top 10

so classics still charting today rank beside his generation's albums. Greatest hits and
compilations are left out (they can never count as heard), as are rows credited only to
"Soundtrack" or "Various Artists". The top TOP entries become the list (QUEUE_SPEC v12):
position = rank, year = first chart year, priority Essential to 250 and Recommended to
1,000, then Deep cut. Both files stay in seeds/lists/, which is not published.
"""

from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

TOP = 2500
LIFETIME_FROM = "2005"
BEFORE_WEIGHT = 0.3
TOP10_BONUS = 2
SHARED = ["position", "artist", "album", "year", "priority", "genre", "descriptors", "note"]
NO_ARTIST = {"soundtrack", "various artists"}
COMPILATION = re.compile(
    r"greatest hits|\bbest of\b|\bhits\b|\bcollection\b|anthology|\bessential\b|\bgold\b"
    r"|number ones|#1'?s|^legend\b|^chronicle\b|^curtain call|the very best"
    r"|^now that'?s|^now!?\s*\d|^kidz bop|^totally hits|^1$|^diamonds$",
    re.IGNORECASE,
)


def clean_artist(name: str) -> str:
    """'"10,000 Maniacs"' → '10,000 Maniacs'; doubled quotes inside collapse."""
    return name.strip().strip('"').replace('""', '"').strip()


def score(recent: int, before: int, top10: int) -> float:
    return recent + BEFORE_WEIGHT * before + TOP10_BONUS * top10


def priority(rank: int) -> str:
    return "Essential" if rank <= 250 else "Recommended" if rank <= 1000 else "Deep cut"


def rank_albums(rows: list[dict[str, str]], top: int = TOP) -> list[dict[str, object]]:
    albums: dict[tuple[str, str], dict[str, object]] = defaultdict(
        lambda: {"recent": 0, "before": 0, "top10": 0, "peak": 999, "first": "9999"}
    )
    for r in rows:
        artist, album = clean_artist(r["Artist"]), r["Song"].strip()
        if not artist or not album or artist.casefold() in NO_ARTIST:
            continue
        if COMPILATION.search(album):
            continue
        a = albums[(artist, album)]
        rank = int(r["Rank"])
        a["peak"] = min(int(a["peak"]), rank)
        a["first"] = min(str(a["first"]), r["Date"])
        a["recent" if r["Date"] >= LIFETIME_FROM else "before"] += 1
        a["top10"] += rank <= 10
    ranked = sorted(
        albums.items(),
        key=lambda kv: (-score(kv[1]["recent"], kv[1]["before"], kv[1]["top10"]), kv[0]),
    )
    out = []
    for i, ((artist, album), a) in enumerate(ranked[:top], 1):
        weeks = int(a["recent"]) + int(a["before"])
        out.append(
            {
                "position": i,
                "artist": artist,
                "album": album,
                "year": str(a["first"])[:4],
                "priority": priority(i),
                "genre": "",
                "descriptors": "",
                "note": f"peak #{a['peak']} · {weeks} weeks ({a['recent']} since {LIFETIME_FROM})",
            }
        )
    return out


def convert(src: Path, dst: Path, top: int = TOP) -> int:
    with src.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    ranked = rank_albums(rows, top)
    with dst.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SHARED)
        w.writeheader()
        w.writerows(ranked)
    return len(ranked)


if __name__ == "__main__":
    print(convert(Path(sys.argv[1]), Path(sys.argv[2])), "albums")
