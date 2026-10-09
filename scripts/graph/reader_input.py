"""Write the independent reader's input: every text-sourced claim as a plain sentence with its quote and source.
Usage: python3 reader_input.py <run_dir> <albums.csv: atlas_id,artist,album>   ->  <run_dir>/reader_input.txt"""

import csv
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
run, albums_p = sys.argv[1], sys.argv[2]
names = {
    r["atlas_id"]: f"{r['artist']} – {r['album']}"
    for r in csv.DictReader(open(albums_p, encoding="utf-8"))
}
T = {
    "sounds_like": "{s} is compared to, or said to sound like or build on, {o}",
    "influenced_by": "{s} was influenced or inspired by {o}",
    "covers": "{s} is a cover of {o}: a later recording of a song that {oa} recorded first",
    "samples": "{s} samples {o}",
    "credited_on": "{s} worked or played on {o}",
    "member_of": "{s} was a member of {o}",
    "associated_with": "{s} worked with {o} ({kind})",
    "has_genre": "{s} is described as {o}",
    "from_scene": "{s} came out of {o}",
    "based_in": "{s} is based in or comes from {o}",
    "recorded_at": "{s} was recorded at {o}",
    "released_by": "{s} was released by {o}",
}


def label(e):
    return e["name"] + (f" ({e['year']})" if e.get("year") else "")


out = []
for line in open(f"{run}/claims_verified.jsonl", encoding="utf-8"):
    c = json.loads(line)
    if c["source"] in ("musicbrainz", "discogs", "wikidata", "firecrawl_json") or c[
        "evidence"
    ].startswith("field:"):
        continue
    s, o, u = c["subject"], c["object"], c["source_url"] or ""
    sent = T[c["predicate"]].format(
        s=label(s),
        o=label(o),
        oa=o.get("artist", o["name"]),
        kind=c["qualifiers"].get("kind", "collaborator"),
    )
    album = names.get(c.get("album"), c.get("album"))
    src = (
        ("Wikipedia article '" + u.split("#")[0].rsplit("/", 1)[1].replace("_", " ") + "'")
        if "wikipedia.org" in u
        else f"Matt's atlas note about {album}"
        if u.startswith("atlas")
        else f"web page {u.split('#')[0]} (about {album})"
    )
    out.append(f'{c["claim_id"]} | CLAIM: {sent} | EVIDENCE: "{c["evidence"]}" | SOURCE: {src}')
open(f"{run}/reader_input.txt", "w", encoding="utf-8").write("\n".join(out) + "\n")
print(len(out), "claims for the reader")
