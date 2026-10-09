"""BestEverAlbums chart download → a list in the shared shape (docs/QUEUE_SPEC.md section 3a).

    uv run python scripts/convert_besteveralbums.py "seeds/lists/BestEverAlbums.com+Overall+Chart.csv" seeds/lists/besteveralbums_overall.csv

The download carries a title row (rank 0) and spreadsheet formulas; the output keeps
rank, artist, album and year, and notes the site's album id and its live / compilation
flags. Both files stay in `seeds/lists/`, which is gitignored (the site's terms).
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

SHARED = ["position", "artist", "album", "year", "priority", "genre", "descriptors", "note"]


def convert(src: Path, dst: Path) -> int:
    with src.open(encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r.get("Rank", "0").isdigit() and r["Rank"] != "0"]
    with dst.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(SHARED)
        for r in rows:
            flags = [k.lower() for k in ("Live", "Compilation") if r.get(k) == "Yes"]
            note = "; ".join([f"BEA album {r['AlbumID']}", *flags])
            w.writerow(
                [r["Rank"], r["Band"].strip(), r["Title"].strip(), r["Year"], "", "", "", note]
            )
    return len(rows)


if __name__ == "__main__":
    print(convert(Path(sys.argv[1]), Path(sys.argv[2])), "rows")
