"""Free baseline for one album: MusicBrainz identity, links, labels, credits, places and works; Wikidata's
Wikipedia sitelink; the album artists' memberships and areas; Discogs credits, labels and styles.
Usage: python3 album_baseline.py <atlas_id> "<artist>" "<album>" <year> <baseline_dir>"""

import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
T = os.path.dirname(os.path.abspath(__file__))
aid, artist, album, year, out = sys.argv[1:6]
os.makedirs(out, exist_ok=True)
p = f"{out}/{aid}.json"
if not os.path.exists(p):
    subprocess.run([sys.executable, f"{T}/mb_baseline.py", artist, album, year, p], check=True)
b = json.load(open(p, encoding="utf-8"))
if b.get("status") != "matched":
    print(aid, "no confident MusicBrainz match:", b.get("candidate"))
    sys.exit(0)
if "discogs" not in b:
    subprocess.run([sys.executable, f"{T}/discogs.py", p], check=False)
for c in b["release_group"]["artist_credit"]:
    ap = f"{out}/artist_{c['mbid']}.json"
    if not os.path.exists(ap):
        subprocess.run([sys.executable, f"{T}/mb_artist.py", c["mbid"], ap], check=True)
b = json.load(open(p, encoding="utf-8"))
print(
    aid,
    b["release_group"]["title"],
    b["release_group"]["first_release_date"],
    "| links:",
    ",".join(sorted(b["links"])) or "none",
    "| MB credits:",
    len(b.get("credits", [])),
    "| Discogs credits:",
    len(b.get("discogs", {}).get("credits", [])),
)
