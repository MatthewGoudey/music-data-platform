"""Artist baseline: type, area, life span, band memberships (both directions). Free.
Usage: python3 mb_artist.py <artist_mbid> <out.json>"""

import json
import sys

sys.path.insert(0, __import__("os").path.dirname(__file__))
from mb_baseline import MB, get  # noqa: E402

a = get(MB + f"artist/{sys.argv[1]}?fmt=json&inc=artist-rels+url-rels")
rels = []
for r in a.get("relations", []):
    if r.get("target-type") == "artist" and r["type"] in (
        "member of band",
        "collaboration",
        "is person",
        "subgroup",
        "supporting musician",
        "vocal supporting musician",
        "instrumental supporting musician",
    ):
        rels.append(
            {
                "type": r["type"],
                "direction": r["direction"],
                "other": r["artist"]["name"],
                "other_mbid": r["artist"]["id"],
                "begin": r.get("begin"),
                "end": r.get("end"),
                "attributes": r.get("attributes", []),
            }
        )
links = {}
for r in a.get("relations", []):
    if r.get("target-type") == "url":
        links.setdefault(r["type"], []).append(r["url"]["resource"])
json.dump(
    {
        "mbid": a["id"],
        "name": a["name"],
        "type": a.get("type"),
        "area": (a.get("area") or {}).get("name"),
        "begin_area": (a.get("begin-area") or {}).get("name"),
        "life_span": a.get("life-span"),
        "relations": rels,
        "links": links,
    },
    open(sys.argv[2], "w", encoding="utf-8"),
    indent=1,
    ensure_ascii=False,
)
