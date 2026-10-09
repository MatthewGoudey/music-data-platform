"""Resolve names to MusicBrainz entities with a date, for direction checks and entity linking. Free.
Usage: python3 mb_resolve.py <in.jsonl> <out.jsonl>
Each input line: {"name": ..., "type": "artist"|"album"|"label"|"place", "artist": <for albums>}"""

import json
import sys
import urllib.parse

sys.path.insert(0, __import__("os").path.dirname(__file__))
from mb_baseline import MB, get, key  # noqa: E402


def resolve(e):
    t, name = e["type"], e["name"]
    if t == "album":
        q = f'releasegroup:"{name}" AND artist:"{e.get("artist", "")}"'
        res = get(MB + "release-group?fmt=json&limit=5&query=" + urllib.parse.quote(q)).get(
            "release-groups", []
        )
        hit = next((r for r in res if key(r["title"]) == key(name)), None)
        return hit and {
            "mbid": hit["id"],
            "date": hit.get("first-release-date"),
            "mb_name": hit["title"],
        }
    if t in ("artist", "person"):
        res = get(
            MB + "artist?fmt=json&limit=5&query=" + urllib.parse.quote(f'artist:"{name}"')
        ).get("artists", [])
        hit = next(
            (
                r
                for r in res
                if key(r["name"]) == key(name)
                or any(key(a.get("name", "")) == key(name) for a in r.get("aliases", []))
            ),
            None,
        )
        return hit and {
            "mbid": hit["id"],
            "date": hit.get("life-span", {}).get("begin"),
            "mb_name": hit["name"],
            "mb_type": hit.get("type"),
            "area": (hit.get("area") or {}).get("name"),
            "disambiguation": hit.get("disambiguation"),
        }
    return None


def resolve_split(e):
    """A joint credit such as "Neil Young & Crazy Horse" is not one MusicBrainz artist: resolve its parts."""
    hit = resolve(e)
    if hit or e["type"] not in ("artist", "person"):
        return hit
    import re

    parts = [x.strip() for x in re.split(r"\s+(?:&|and|with)\s+", e["name"]) if x.strip()]
    if len(parts) < 2:
        return None
    found = [resolve({"type": "artist", "name": x}) for x in parts]
    found = [f for f in found if f]
    if not found:
        return None
    dates = sorted(f["date"] for f in found if f.get("date"))
    return {
        "mbid": found[0]["mbid"],
        "date": dates[0] if dates else None,
        "mb_name": " + ".join(f["mb_name"] for f in found),
        "split_credit": [f["mbid"] for f in found],
    }


out = open(sys.argv[2], "w", encoding="utf-8")
for line in open(sys.argv[1], encoding="utf-8"):
    e = json.loads(line)
    try:
        e["mb"] = resolve_split(e)
    except Exception as ex:
        e["mb"] = None
        e["error"] = str(ex)[:120]
    out.write(json.dumps(e, ensure_ascii=False) + "\n")
