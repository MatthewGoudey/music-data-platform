"""Turn the MusicBrainz baseline and Firecrawl facts JSON into proposed claims (edge-vocabulary section 3).
Usage: python3 facts_to_claims.py <atlas_id> <album display name> <baseline.json> <cache_dir> <run_id> <out.jsonl>"""

import datetime
import glob
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
sys.path.insert(0, os.path.dirname(__file__))
from mb_baseline import key  # noqa: E402

aid, disp, base_p, cache, run_id, out_p = sys.argv[1:7]
b = json.load(open(base_p, encoding="utf-8"))
today = datetime.date.today().isoformat()
album = {
    "type": "album",
    "name": disp,
    "atlas_id": aid,
    "mbid": b.get("release_group", {}).get("mbid"),
}
rel_url = (
    f"https://musicbrainz.org/release/{b['canonical_release']['mbid']}"
    if b.get("canonical_release")
    else None
)
claims = []


def claim(subject, predicate, obj, qualifiers, source, basis, evidence, url):
    claims.append(
        {
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "qualifiers": qualifiers,
            "source": source,
            "basis": basis,
            "evidence": evidence[:300],
            "source_url": url,
            "status": "proposed",
            "asserted_by": "music-graph-research",
            "asserted_at": today,
            "run_id": run_id,
            "album": aid,
        }
    )


def role_of(c):
    attrs = [a for a in c["attributes"] if a not in ("guest", "additional")]
    if c["role"] in ("instrument", "vocal"):
        return attrs or [c["role"]]
    return [("co-" if "co" in attrs else "") + c["role"]]


# MusicBrainz: credits (album + recording level collapsed to one claim per person), places, labels
by_person = {}
for c in b.get("credits", []):
    p = by_person.setdefault(c["mbid"], {"name": c["name"], "roles": set(), "tracks": set()})
    p["roles"].update(role_of(c))
    if c["track"]:
        p["tracks"].add(c["track"])
for mbid, p in by_person.items():
    claim(
        {"type": "person", "name": p["name"], "mbid": mbid},
        "credited_on",
        album,
        {"role": sorted(p["roles"]), "tracks": len(p["tracks"]) or "album"},
        "musicbrainz",
        "documented",
        f"MusicBrainz release credits: {p['name']} — {', '.join(sorted(p['roles']))}",
        rel_url,
    )
writers = {}
for w in b.get("works", []):
    for wr in w["writers"]:
        writers.setdefault(wr["mbid"], {"name": wr["name"], "tracks": []})["tracks"].append(
            w["track"]
        )
for mbid, wr in writers.items():
    claim(
        {"type": "person", "name": wr["name"], "mbid": mbid},
        "credited_on",
        album,
        {"role": ["songwriter"], "tracks": wr["tracks"]},
        "musicbrainz",
        "documented",
        f"MusicBrainz works written by {wr['name']}: {', '.join(wr['tracks'])}",
        rel_url,
    )
seen = set()
for pl in b.get("places", []):
    if (pl["mbid"], pl["role"]) in seen:
        continue
    seen.add((pl["mbid"], pl["role"]))
    claim(
        album,
        "recorded_at",
        {"type": "place", "name": pl["name"], "mbid": pl["mbid"]},
        {"mb_role": pl["role"], "dates": pl.get("begin")},
        "musicbrainz",
        "documented",
        f"MusicBrainz: {pl['role']} {pl['name']}",
        rel_url,
    )
for lb in b.get("labels", []):
    claim(
        album,
        "released_by",
        {"type": "label", "name": lb["name"], "mbid": lb["mbid"]},
        {"catalog": lb.get("catalog")},
        "musicbrainz",
        "documented",
        f"MusicBrainz label info: {lb['name']} {lb.get('catalog') or ''}".strip(),
        rel_url,
    )
for art in glob.glob(os.path.join(os.path.dirname(base_p), "artist_*.json")):
    a = json.load(open(art, encoding="utf-8"))
    if a["mbid"] not in {c["mbid"] for c in b.get("release_group", {}).get("artist_credit", [])}:
        continue
    done = set()
    for r in a["relations"]:
        if r["type"] != "member of band" or r["other_mbid"] in done:
            continue
        done.add(r["other_mbid"])
        person, group = (
            ((a["name"], a["mbid"]), (r["other"], r["other_mbid"]))
            if r["direction"] == "forward"
            else ((r["other"], r["other_mbid"]), (a["name"], a["mbid"]))
        )
        claim(
            {"type": "person", "name": person[0], "mbid": person[1]},
            "member_of",
            {"type": "artist", "name": group[0], "mbid": group[1]},
            {"from": r["begin"], "to": r["end"], "instrument": r["attributes"]},
            "musicbrainz",
            "documented",
            f"MusicBrainz: {person[0]} member of band {group[0]}",
            f"https://musicbrainz.org/artist/{a['mbid']}",
        )
    if a.get("area") or a.get("begin_area"):
        claim(
            {"type": "artist", "name": a["name"], "mbid": a["mbid"]},
            "based_in",
            {"type": "area", "name": a.get("begin_area") or a["area"]},
            {},
            "musicbrainz",
            "documented",
            f"MusicBrainz area: {a.get('area')}; begin area: {a.get('begin_area')}",
            f"https://musicbrainz.org/artist/{a['mbid']}",
        )

# Discogs: credits (one claim per person), labels, styles
dg = b.get("discogs")
if dg:
    import re

    people = {}
    for c in dg["credits"]:
        n = re.sub(r"\s\(\d+\)$", "", c["name"])
        people.setdefault(n, set()).add(re.sub(r"\s*\[.*?\]", "", c["role"] or "").strip())
    for n, roles in people.items():
        claim(
            {"type": "person", "name": n},
            "credited_on",
            album,
            {"role": sorted(roles)},
            "discogs",
            "documented",
            f"Discogs credits: {n} — {', '.join(sorted(roles))}",
            dg["url"],
        )
    for ln in dg["labels"]:
        claim(
            album,
            "released_by",
            {"type": "label", "name": re.sub(r"\s\(\d+\)$", "", ln["name"])},
            {"catalog": ln.get("catno")},
            "discogs",
            "documented",
            f"Discogs label: {ln['name']} {ln.get('catno') or ''}".strip(),
            dg["url"],
        )
    for st in dg["styles"]:
        claim(
            album,
            "has_genre",
            {"type": "genre", "name": st},
            {"vocabulary": "discogs_style"},
            "discogs",
            "documented",
            f"Discogs style: {st}",
            dg["url"],
        )

# Firecrawl JSON facts (Wikipedia personnel and recording facts)
for idx in open(os.path.join(cache, "index.jsonl"), encoding="utf-8"):
    r = json.loads(idx)
    if r["entity"] != aid or not r.get("facts"):
        continue
    f = json.load(
        open(os.path.join(cache, r["file"].replace(".md", ".facts.json")), encoding="utf-8")
    )
    url = r["url"]
    people = {}
    for p in f.get("producers", []):
        people.setdefault(p, set()).add("producer")
    for e in f.get("engineers_and_mixers", []):
        people.setdefault(e["name"], set()).update(x.strip() for x in e["role"].split(","))
    for m in f.get("musicians", []):
        people.setdefault(m["name"], set()).update(m["roles"])
    for name, roles in people.items():
        claim(
            {"type": "person", "name": name},
            "credited_on",
            album,
            {"role": sorted(roles)},
            "firecrawl_json",
            "documented",
            f"Wikipedia personnel: {name} — {', '.join(sorted(roles))}",
            url,
        )
    for loc in f.get("recording_locations", []):
        claim(
            album,
            "recorded_at",
            {"type": "place", "name": loc["studio_or_place"], "city": loc.get("city")},
            {"dates": f.get("recording_dates") or None},
            "firecrawl_json",
            "documented",
            f"Wikipedia: recorded at {loc['studio_or_place']}, {loc.get('city')}",
            url,
        )


# Cross-source agreement: same subject, predicate and object from another source
def k(e):
    return key(e.get("name", ""))


def same(a, b):
    ka, kb = k(a), k(b)
    return ka == kb or (len(min(ka, kb, key=len)) >= 5 and (ka in kb or kb in ka))


for c in claims:
    c["corroborated_by"] = sorted(
        {
            o["source"]
            for o in claims
            if o is not c
            and o["source"] != c["source"]
            and o["predicate"] == c["predicate"]
            and same(o["subject"], c["subject"])
            and same(o["object"], c["object"])
        }
    )
with open(out_p, "w", encoding="utf-8") as fo:
    for i, c in enumerate(claims, 1):
        c["claim_id"] = f"{run_id}-{aid}-F{i:03d}"
        fo.write(json.dumps(c, ensure_ascii=False) + "\n")
print(aid, len(claims), "claims;", sum(1 for c in claims if c["corroborated_by"]), "corroborated")
