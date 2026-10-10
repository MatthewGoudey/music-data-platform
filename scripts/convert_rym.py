"""Convert a Rate Your Music top-albums chart CSV (Matt's download, `rym_clean1.csv`: position,
release_name, artist_name, release_date, release_type, primary_genres, secondary_genres,
descriptors, avg_rating, rating_count, review_count) to the shared list shape (QUEUE_SPEC v13).

Albums only; the genres and RYM's descriptors carry over, and the note keeps the rating and
its count.

Usage: uv run python scripts/convert_rym.py seeds/lists/rym_clean1.csv seeds/lists/rym_top_5000.csv
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

SHARED = ["position", "artist", "album", "year", "priority", "genre", "descriptors", "note"]


def _genres(row: dict) -> str:
    genres = []
    for field in ("primary_genres", "secondary_genres"):
        for g in (row.get(field) or "").split(","):
            g = g.strip()
            if g and g != "NA" and g not in genres:
                genres.append(g)
    return ", ".join(genres)


def convert(src: Path, dst: Path) -> int:
    with src.open(encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("release_type") or "album") == "album"]
    with dst.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(SHARED)
        for r in rows:
            note = f"RYM average {r['avg_rating']} from {r['rating_count']} ratings"
            descriptors = "" if r.get("descriptors") == "NA" else r.get("descriptors", "")
            w.writerow(
                [r["position"], r["artist_name"].strip(), r["release_name"].strip(),
                 r["release_date"][:4], "", _genres(r), descriptors, note]
            )  # fmt: skip
    return len(rows)


if __name__ == "__main__":
    print(convert(Path(sys.argv[1]), Path(sys.argv[2])), "rows")
