"""MusicBrainz + Wikidata baseline for one album: identity, links, labels, credits, places, covers.

Usage: python3 mb_baseline.py "<artist>" "<album>" <year> <out.json>
Free (no Firecrawl). 1 request/second with a real User-Agent, per MusicBrainz rules.
"""

import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

UA = "musicdata-graph/0.1 (https://github.com/MatthewGoudey/music-data-platform)"
MB = "https://musicbrainz.org/ws/2/"
_last = [0.0]


def get(url):
    wait = 1.05 - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(
        4
    ):  # MusicBrainz (503) and Wikidata (429) rate limits: back off, then retry.
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                _last[0] = time.time()
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503) or attempt == 3:
                raise
            time.sleep(int(e.headers.get("Retry-After") or 0) or 2 * (attempt + 1))


def key(s):
    s = unicodedata.normalize("NFKD", s or "").lower()
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("&", "and")
    return re.sub(r"[^\w]+", " ", s).strip()


def find_release_group(artist, album, year):
    q = f'releasegroup:"{album}" AND artist:"{artist}"'
    res = get(MB + "release-group?fmt=json&limit=10&query=" + urllib.parse.quote(q))
    best, best_score = None, -1
    for rg in res.get("release-groups", []):
        credit = " ".join(
            c.get("name", "") + c.get("joinphrase", "") for c in rg.get("artist-credit", [])
        )
        score = 0
        score += 4 if key(rg["title"]) == key(album) else 0
        score += 3 if key(artist) in key(credit) or key(credit) in key(artist) else 0
        y = (rg.get("first-release-date") or "")[:4]
        score += 2 if y and year and abs(int(y) - int(year)) <= 1 else 0
        score += 1 if rg.get("primary-type") in ("Album", "EP") else 0
        if score > best_score:
            best, best_score = rg, score
    return best, best_score


def main(artist, album, year, out):
    rg, score = find_release_group(artist, album, year)
    result = {"query": {"artist": artist, "album": album, "year": year}, "match_score": score}
    if not rg or score < 7:
        result["status"] = "no_confident_match"
        result["candidate"] = rg and {"id": rg["id"], "title": rg["title"]}
        json.dump(result, open(out, "w", encoding="utf-8"), indent=1)
        return
    mbid = rg["id"]
    rgd = get(MB + f"release-group/{mbid}?fmt=json&inc=url-rels+artist-credits")
    links = {}
    for rel in rgd.get("relations", []):
        u = rel.get("url", {}).get("resource")
        if u:
            links.setdefault(rel["type"], []).append(u)
    # Canonical release: official, earliest, then fewest tracks (edge rules).
    rels = get(MB + f"release?fmt=json&limit=100&inc=media&release-group={mbid}").get(
        "releases", []
    )
    off = [r for r in rels if r.get("status") == "Official"] or rels

    def tracks(r):
        return sum(m.get("track-count", 0) for m in r.get("media", []))

    off.sort(key=lambda r: ((r.get("date") or "9999")[:7], tracks(r)))
    first = (off[0].get("date") or "")[:4] if off else ""
    early = [
        r
        for r in off
        if (r.get("date") or "")[:4] and first and int(r["date"][:4]) - int(first) <= 1
    ] or off
    canon = min(early, key=tracks) if early else None
    result.update(
        {
            "status": "matched",
            "release_group": {
                "mbid": mbid,
                "title": rgd["title"],
                "primary_type": rgd.get("primary-type"),
                "secondary_types": rgd.get("secondary-types", []),
                "first_release_date": rgd.get("first-release-date"),
                "artist_credit": [
                    {"name": c["name"], "mbid": c["artist"]["id"]}
                    for c in rgd.get("artist-credit", [])
                ],
            },
            "links": links,
        }
    )
    if canon:
        inc = "labels+recordings+artist-rels+place-rels+recording-level-rels+work-rels+work-level-rels+artist-credits"
        rd = get(MB + f"release/{canon['id']}?fmt=json&inc={inc}")
        result["canonical_release"] = {
            "mbid": canon["id"],
            "date": rd.get("date"),
            "country": rd.get("country"),
            "track_count": tracks(canon),
        }
        result["labels"] = [
            {
                "name": li["label"]["name"],
                "mbid": li["label"]["id"],
                "catalog": li.get("catalog-number"),
            }
            for li in rd.get("label-info", [])
            if li.get("label")
        ]
        credits, places, works = [], [], []

        def take(rels, scope, track=None):
            for rel in rels:
                if rel.get("target-type") == "artist":
                    credits.append(
                        {
                            "name": rel["artist"]["name"],
                            "mbid": rel["artist"]["id"],
                            "role": rel["type"],
                            "attributes": rel.get("attributes", []),
                            "scope": scope,
                            "track": track,
                        }
                    )
                elif rel.get("target-type") == "place":
                    places.append(
                        {
                            "name": rel["place"]["name"],
                            "mbid": rel["place"]["id"],
                            "role": rel["type"],
                            "begin": rel.get("begin"),
                            "end": rel.get("end"),
                            "scope": scope,
                            "track": track,
                        }
                    )

        take(rd.get("relations", []), "release")
        for medium in rd.get("media", []):
            for t in medium.get("tracks", []):
                rec = t.get("recording", {})
                take(rec.get("relations", []), "recording", t.get("title"))
                for rel in rec.get("relations", []):
                    if rel.get("target-type") == "work":
                        w = rel["work"]
                        writers = list(
                            {
                                wr["artist"]["id"]: {
                                    "name": wr["artist"]["name"],
                                    "mbid": wr["artist"]["id"],
                                }
                                for wr in w.get("relations", [])
                                if wr.get("target-type") == "artist"
                                and wr["type"] in ("composer", "lyricist", "writer")
                            }.values()
                        )
                        works.append(
                            {
                                "track": t.get("title"),
                                "work": w["title"],
                                "work_mbid": w["id"],
                                "cover_attr": "cover" in rel.get("attributes", []),
                                "writers": writers,
                            }
                        )
        result["credits"], result["places"], result["works"] = credits, places, works
    # Wikidata sitelink for English Wikipedia when MusicBrainz has no direct Wikipedia link.
    wd = [u for u in links.get("wikidata", [])]
    if wd and "wikipedia" not in links:
        qid = wd[0].rsplit("/", 1)[-1]
        try:
            ent = get(
                f"https://www.wikidata.org/w/api.php?action=wbgetentities&ids={qid}&props=sitelinks/urls&sitefilter=enwiki&format=json"
            )
            sl = ent["entities"][qid].get("sitelinks", {}).get("enwiki")
            if sl:
                links.setdefault("wikipedia", []).append(sl["url"])
        except Exception as e:  # logged by the caller as a gap
            result["wikidata_error"] = str(e)[:200]
    json.dump(result, open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
