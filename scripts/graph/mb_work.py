"""Every recording of a work, with artist, first release date and the cover attribute: the documented way to
check `covers` and its direction. Free. Usage: python3 mb_work.py <work_mbid> [<work_mbid> ...]"""

import json
import sys

sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
sys.path.insert(0, __import__("os").path.dirname(__file__))
from mb_baseline import MB, get  # noqa: E402

for wid in sys.argv[1:]:
    w = get(MB + f"work/{wid}?fmt=json&inc=recording-rels+artist-rels")
    writers = sorted(
        {r["artist"]["name"] for r in w.get("relations", []) if r.get("target-type") == "artist"}
    )
    attrs = {
        r["recording"]["id"]: r.get("attributes", [])
        for r in w.get("relations", [])
        if r.get("target-type") == "recording"
    }
    recs, offset = [], 0
    while True:  # browse gives artist credits and first release dates, which the work lookup lacks
        page = get(
            MB + f"recording?work={wid}&inc=artist-credits&fmt=json&limit=100&offset={offset}"
        )
        for rec in page.get("recordings", []):
            a = attrs.get(rec["id"], [])
            recs.append(
                {
                    "recording": rec["id"],
                    "title": rec["title"],
                    "artist": "".join(
                        c["name"] + c.get("joinphrase", "") for c in rec.get("artist-credit", [])
                    ),
                    "first_release": rec.get("first-release-date"),
                    "cover": "cover" in a,
                    "live": "live" in a,
                }
            )
        offset += 100
        if offset >= page.get("recording-count", 0):
            break
    # one row per artist: their earliest recording of the work
    first = {}
    for r in sorted(recs, key=lambda x: x["first_release"] or "9999"):
        first.setdefault(r["artist"], r)
    recs = list(first.values())
    print(
        json.dumps(
            {
                "work": wid,
                "title": w["title"],
                "writers": writers,
                "recordings": len(recs),
                "sample": sorted(recs, key=lambda x: x["first_release"] or "9999")[:40],
            },
            ensure_ascii=False,
        )
    )
