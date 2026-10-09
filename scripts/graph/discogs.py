"""Discogs credits, labels and styles for an album, from the Discogs link MusicBrainz holds. Free, 25 requests/minute
without a token (60 with DISCOGS_TOKEN). Usage: python3 discogs.py <baseline.json>  (adds a "discogs" key in place)"""

import json
import os
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
UA = "musicdata-graph/0.1 +https://github.com/MatthewGoudey/music-data-platform"


def get(url):
    h = {"User-Agent": UA}
    if os.environ.get("DISCOGS_TOKEN"):
        h["Authorization"] = "Discogs token=" + os.environ["DISCOGS_TOKEN"]
    time.sleep(2.5)  # stays under 25/minute
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
        return json.load(r)


p = sys.argv[1]
b = json.load(open(p, encoding="utf-8"))
links = b.get("links", {}).get("discogs", [])
master = next((u for u in links if "/master/" in u), None)
release = next((u for u in links if "/release/" in u), None)
if master:
    m = get("https://api.discogs.com/masters/" + master.rstrip("/").rsplit("/", 1)[-1])
    rid = m.get("main_release")
elif release:
    rid = release.rstrip("/").rsplit("/", 1)[-1]
else:
    print("no discogs link")
    sys.exit(0)
r = get(f"https://api.discogs.com/releases/{rid}")
credits = [
    {
        "name": a["name"],
        "anv": a.get("anv"),
        "id": a.get("id"),
        "role": a.get("role"),
        "tracks": a.get("tracks") or "album",
    }
    for a in r.get("extraartists", [])
]
for t in r.get("tracklist", []):
    for a in t.get("extraartists", []) or []:
        credits.append(
            {
                "name": a["name"],
                "anv": a.get("anv"),
                "id": a.get("id"),
                "role": a.get("role"),
                "tracks": t.get("title"),
            }
        )
b["discogs"] = {
    "release_id": rid,
    "url": f"https://www.discogs.com/release/{rid}",
    "styles": r.get("styles", []),
    "genres": r.get("genres", []),
    "labels": [{"name": ln["name"], "catno": ln.get("catno")} for ln in r.get("labels", [])],
    "credits": credits,
    "notes": (r.get("notes") or "")[:1500],
}
json.dump(b, open(p, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print(p, rid, len(credits), "credits", r.get("styles"))
