"""Checks that need no music knowledge: structure, verbatim evidence, date direction, database cross-checks,
source independence, the independent reader's verdicts; then confidence and a status.
Usage: python3 verify.py <run_dir> <atlas_notes.csv> [<cache_dir> <baseline_dir>]
Reads <run_dir>/claims/*.jsonl and <run_dir>/reader_output.jsonl; writes <run_dir>/claims_verified.jsonl."""

import csv
import glob
import json
import os
import re
import subprocess
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252

sys.path.insert(0, os.path.dirname(__file__))
from mb_baseline import key as _key  # noqa: E402


def key(s):
    return _key((s or "").replace("'", "").replace("’", ""))


run, notes_p = sys.argv[1], sys.argv[2]
CACHE = sys.argv[3] if len(sys.argv) > 3 else f"{run}/cache"
BASE_DIR = sys.argv[4] if len(sys.argv) > 4 else f"{run}/baseline"
T = os.path.dirname(os.path.abspath(__file__))
ART = {"artist", "person"}
PRED = {
    "in_lane": ({"album"}, {"lane"}),
    "lane_parent": ({"lane"}, {"lane"}),
    "has_genre": ({"album"} | ART, {"genre"}),
    "from_scene": ({"album"} | ART, {"scene"}),
    "based_in": (ART, {"area"}),
    "recorded_at": ({"album"}, {"place", "area"}),
    "member_of": (ART, {"artist"}),
    "credited_on": (ART, {"album", "recording"}),
    "released_by": ({"album"}, {"label"}),
    "influenced_by": ({"album"} | ART, {"album"} | ART),
    "sounds_like": ({"album"}, {"album"} | ART),
    "covers": ({"recording", "album"}, {"work", "recording", "album"}),
    "samples": ({"recording"}, {"recording"}),
    "associated_with": (ART, ART),
    "path_next": ({"album"}, {"album"}),
    "has_tag": ({"album"}, {"tag"}),
    "on_list": ({"album"}, {"list"}),
}
LINEAGE = {"influenced_by", "sounds_like", "covers", "samples"}
BASE = {
    ("musicbrainz", "documented"): 0.9,
    ("discogs", "documented"): 0.9,
    ("wikidata", "documented"): 0.9,
    ("firecrawl_json", "documented"): 0.75,
    ("map", "reported"): 0.7,
    ("map", "inferred"): 0.6,
    ("text", "documented"): 0.8,
    ("text", "reported"): 0.7,
    ("text", "inferred"): 0.5,
}


def norm(s):
    url = (
        r"\((?:[^()\s]|\([^()\s]*\))*(?:\s+\"[^\"]*\")?\)"  # a link target with an optional "title"
    )
    s = re.sub(
        r"\[\\?\[\d+\\?\]\]" + url, "", s or ""
    )  # citation links like [\[4\]](...#cite_note-4)
    s = re.sub(r"\[([^\]]*)\]" + url, r"\1", s)  # [text](url) -> text
    s = re.sub(r' "[^"]+"\)', "", s)  # Wikipedia link-title remnants
    s = re.sub(r"\[\d+\]", "", s)
    s = (
        s.replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("_", "")
        .replace("*", "")
    )
    return re.sub(r"\s+", " ", s).strip().lower()


notes = {r["atlas_id"]: r for r in csv.DictReader(open(notes_p, encoding="utf-8"))}
index = [json.loads(ln) for ln in open(f"{CACHE}/index.jsonl", encoding="utf-8")]
page = {
    r["url"]: norm(open(f"{CACHE}/{r['file']}", encoding="utf-8").read())
    for r in index
    if not r.get("note")
}  # notes mark superseded fetches
claims = [
    json.loads(ln)
    for f in sorted(glob.glob(f"{run}/claims/*.jsonl"))
    if "verified" not in f and "skipped" not in f
    for ln in open(f, encoding="utf-8")
]
works = {}
for f in glob.glob(f"{BASE_DIR}/*_works.jsonl"):
    for ln in open(f, encoding="utf-8"):
        w = json.loads(ln)
        works[key(w["title"].split("(")[0])] = w
artists = {
    key(json.load(open(f, encoding="utf-8"))["name"]): json.load(open(f, encoding="utf-8"))
    for f in glob.glob(f"{BASE_DIR}/artist_*.json")
}


def resolve(entities):
    """MusicBrainz first-release / begin dates for entities not in hand (free, cached per run)."""
    cache_p = f"{BASE_DIR}/resolved.jsonl"
    have = (
        {
            (r["type"], r["name"], r.get("artist", "")): r
            for r in map(json.loads, open(cache_p, encoding="utf-8"))
        }
        if os.path.exists(cache_p)
        else {}
    )
    todo = [e for e in entities if (e["type"], e["name"], e.get("artist", "")) not in have]
    if todo:
        open(f"{BASE_DIR}/_todo.jsonl", "w", encoding="utf-8").write(
            "".join(json.dumps(e) + "\n" for e in todo)
        )
        subprocess.run(
            [
                sys.executable,
                f"{T}/mb_resolve.py",
                f"{BASE_DIR}/_todo.jsonl",
                f"{BASE_DIR}/_new.jsonl",
            ],
            check=True,
        )
        with open(cache_p, "a", encoding="utf-8") as f:
            for ln in open(f"{BASE_DIR}/_new.jsonl", encoding="utf-8"):
                r = json.loads(ln)
                have[(r["type"], r["name"], r.get("artist", ""))] = r
                f.write(ln)
    return have


def year_of(e, have):
    if e.get("year"):
        return int(e["year"])
    if e["type"] in ("album", "artist", "person"):
        t = e["type"] if e["type"] != "album" else "album"
        nm = e["name"].split(" – ", 1)[-1] if t == "album" else e["name"]
        r = have.get((t, nm, e.get("artist", "")))
        d = r and r.get("mb") and r["mb"].get("date")
        return int(d[:4]) if d else None
    return None


def fetch_missing():
    """Free database lookups for cross-checks: memberships of people in member_of claims, and the credits of
    other albums named in credited_on claims."""
    have_artists = set(artists)
    todo_people = {
        c["subject"]["name"]
        for c in claims
        if c["predicate"] == "member_of" and key(c["subject"]["name"]) not in have_artists
    }
    if todo_people:
        open(f"{BASE_DIR}/_p.jsonl", "w", encoding="utf-8").write(
            "".join(json.dumps({"type": "artist", "name": n}) + "\n" for n in todo_people)
        )
        subprocess.run(
            [
                sys.executable,
                f"{T}/mb_resolve.py",
                f"{BASE_DIR}/_p.jsonl",
                f"{BASE_DIR}/_p_out.jsonl",
            ],
            check=True,
        )
        for ln in open(f"{BASE_DIR}/_p_out.jsonl", encoding="utf-8"):
            r = json.loads(ln)
            if r.get("mb"):
                out = f"{BASE_DIR}/artist_{r['mb']['mbid']}.json"
                if not os.path.exists(out):
                    subprocess.run(
                        [sys.executable, f"{T}/mb_artist.py", r["mb"]["mbid"], out], check=True
                    )
                a = json.load(open(out, encoding="utf-8"))
                artists[key(a["name"])] = a
    for c in claims:
        if c["predicate"] == "covers" and c.get("album"):
            title = c["qualifiers"].get("work") or c["object"]["name"].split(" – ")[-1]
            if key(title.split("(")[0]) not in works:
                bl = json.load(open(f"{BASE_DIR}/{c['album']}.json", encoding="utf-8"))
                w = next(
                    (
                        w
                        for w in bl.get("works", [])
                        if key(w["track"]) == key(title)
                        or key(w["work"].split("(")[0]) == key(title.split("(")[0])
                    ),
                    None,
                )
                if w:
                    res = subprocess.run(
                        [sys.executable, f"{T}/mb_work.py", w["work_mbid"]],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        check=True,
                    ).stdout
                    with open(f"{BASE_DIR}/{c['album']}_works.jsonl", "a", encoding="utf-8") as f:
                        f.write(res)
                    wj = json.loads(res)
                    works[key(wj["title"].split("(")[0])] = wj
    for c in claims:
        o = c["object"]
        if c["predicate"] == "credited_on" and o["type"] == "album" and not o.get("atlas_id"):
            slug = re.sub(r"[^a-z0-9]+", "_", key(o["name"]))[:60]
            out = f"{BASE_DIR}/X_{slug}.json"
            if not os.path.exists(out):
                subprocess.run(
                    [
                        sys.executable,
                        f"{T}/mb_baseline.py",
                        o["artist"],
                        o["name"].split(" – ", 1)[1],
                        str(o.get("year", "")),
                        out,
                    ],
                    check=True,
                )
                subprocess.run([sys.executable, f"{T}/discogs.py", out], check=False)
            foreign[o["name"]] = json.load(open(out, encoding="utf-8"))


foreign = {}
fetch_missing()

need = []
for c in claims:
    if c["predicate"] in LINEAGE:
        for e in (c["subject"], c["object"]):
            if e["type"] in ART and not e.get("year"):
                need.append({"type": e["type"], "name": e["name"]})
have = resolve(need) if need else {}
album_years = {
    a: json.load(open(f"{BASE_DIR}/{a}.json", encoding="utf-8"))
    .get("release_group", {})
    .get("first_release_date", "")[:4]
    for a in {os.path.basename(f)[:5] for f in glob.glob(f"{BASE_DIR}/A*.json")}
}

reader = {}
if os.path.exists(f"{run}/reader_output.jsonl"):
    reader = {
        r["claim_id"]: r
        for r in map(json.loads, open(f"{run}/reader_output.jsonl", encoding="utf-8"))
    }
for c in claims:
    chk, fails = {}, []
    s, p, o = c["subject"], c["predicate"], c["object"]
    # 1. structure
    if p not in PRED:
        fails.append("unknown predicate")
    else:
        st, ot = PRED[p]
        if s["type"] not in st:
            fails.append(f"subject type {s['type']} not allowed for {p}")
        if o["type"] not in ot:
            fails.append(f"object type {o['type']} not allowed for {p}")
    if p == "sounds_like" and c["basis"] != "inferred":
        fails.append("sounds_like must be inferred")
    if p == "influenced_by" and c["basis"] not in ("documented", "reported"):
        fails.append("influenced_by needs documented or reported evidence")
    if p in LINEAGE and c.get("extractor") == "claude" and c.get("direction") != "subject_newer":
        fails.append("lineage claim without direction")
    chk["structure"] = "pass" if not fails else "fail"
    # 2. verbatim evidence for text sources
    src_kind = (
        "db"
        if c["source"] in ("musicbrainz", "discogs", "wikidata")
        else "map"
        if c["source"].startswith("map:")
        else "firecrawl_json"
        if c["source"] == "firecrawl_json"
        else "text"
    )
    if (
        src_kind == "firecrawl_json"
    ):  # extraction can invent: the name must appear on the page it came from
        nm = norm(o["name"] if p == "recorded_at" else s["name"])
        chk["name_on_page"] = "pass" if nm and nm in page.get(c["source_url"], "") else "fail"
        if chk["name_on_page"] == "fail":
            fails.append("extracted name not on the cached page")
    if c["evidence"].startswith("field:"):
        val = norm(c["evidence"].split("=", 1)[-1])
        chk["evidence_field"] = (
            "pass"
            if val
            and val in page.get(c["source_url"].split("#")[0], "") + page.get(c["source_url"], "")
            or any(val in v for k, v in page.items() if k.startswith(c["source_url"]))
            else "fail"
        )
        if chk["evidence_field"] == "fail":
            fails.append("field value not on the cached page")
    elif src_kind in ("text", "map"):
        ev = norm(c["evidence"])
        if src_kind == "map":
            n = notes.get(c["source_url"].split("#")[1].split(".")[0], {})
            hay = norm(n.get("description", "") + " " + n.get("lineage", ""))
        else:
            hay = page.get(c["source_url"], "")
        segs = [x.strip() for x in ev.split("…") if x.strip()]
        chk["evidence_verbatim"] = "pass" if segs and all(x in hay for x in segs) else "fail"
        if chk["evidence_verbatim"] == "fail":
            fails.append("evidence not found verbatim in the cached source")
        if len(c["evidence"]) > 300:
            fails.append("evidence over 300 characters")
    # 3. direction by dates (lineage): the object must be older than the subject
    if p in LINEAGE:
        sy = year_of(s, have) or (
            int(album_years[c["album"]])
            if s.get("atlas_id") and album_years.get(c["album"])
            else None
        )
        oy = year_of(o, have) or (
            int(album_years[c["album"]])
            if o.get("atlas_id") and album_years.get(c["album"])
            else None
        )
        chk["dates"] = {"subject": sy, "object": oy}
        if sy and oy:
            chk["direction"] = "pass" if oy <= sy else "fail"
            if oy > sy:
                fails.append(f"direction: object ({oy}) is newer than subject ({sy})")
        else:
            chk["direction"] = "unknown"
    # 4. database cross-checks
    db = []
    if p == "covers":
        w = works.get(key(o.get("name", "").split(" – ")[-1].split("(")[0]))
        if w:
            recs = {key(r["artist"]): r for r in w["sample"]}
            rs, ro = recs.get(key(s.get("artist", ""))), recs.get(key(o.get("artist", "")))
            dated = [r for r in w["sample"] if r["first_release"]]
            earliest = min(dated, key=lambda r: r["first_release"]) if dated else None
            if (
                earliest
                and ro
                and key(earliest["artist"]) != key(ro["artist"])
                and earliest["first_release"][:4] < (ro["first_release"] or "9999")[:4]
            ):
                fails.append(
                    f"object is not the first recording: {earliest['artist']} ({earliest['first_release']}) is earlier"
                )
            if rs and ro and rs["first_release"] and ro["first_release"]:
                ok = ro["first_release"] <= rs["first_release"]
                db.append("musicbrainz" if ok else "musicbrainz:contradicts")
                chk["mb_work"] = (
                    f"{o['artist']} {ro['first_release']} → {s['artist']} {rs['first_release']}"
                    + (" (cover)" if rs["cover"] else "")
                )
            else:
                chk["mb_work"] = "recording not in MusicBrainz for " + (
                    s.get("artist") if not rs else o.get("artist")
                )
    if p == "member_of":
        a = artists.get(key(o["name"])) or artists.get(key(s["name"]))
        if a:
            other = s["name"] if key(a["name"]) == key(o["name"]) else o["name"]
            hit = any(
                r["type"] == "member of band" and key(r["other"]) == key(other)
                for r in a["relations"]
            )
            db.append("musicbrainz" if hit else "musicbrainz:absent")
    if p == "credited_on" and o["name"] in foreign:
        fb = foreign[o["name"]]
        names = {key(x["name"]) for x in fb.get("credits", [])} | {
            key(x["name"]) for x in fb.get("release_group", {}).get("artist_credit", [])
        }
        dnames = {
            key(re.sub(r"\s\(\d+\)$", "", x["name"]))
            for x in fb.get("discogs", {}).get("credits", [])
        }
        if key(s["name"]) in names:
            db.append("musicbrainz")
        if key(s["name"]) in dnames:
            db.append("discogs")
        chk["foreign_album"] = fb.get("status")
    if c["source"] not in ("musicbrainz", "discogs", "wikidata") and p in (
        "credited_on",
        "member_of",
        "released_by",
        "recorded_at",
    ):
        db += [x for x in c.get("corroborated_by", []) if x in ("musicbrainz", "discogs")]
    # 5. independence: an atlas note built from the same page is not a second source
    indep = set(x for x in c.get("corroborated_by", []))
    same_edge = [
        d
        for d in claims
        if d is not c
        and d["predicate"] == p
        and key(d["subject"]["name"]) == key(s["name"])
        and key(d["object"]["name"]) == key(o["name"])
    ]
    for d in same_edge:
        if d["source"] == c["source"]:
            continue
        a_src = (
            c if c["source"].startswith("map:") else d if d["source"].startswith("map:") else None
        )
        other = d if a_src is c else c
        if a_src is not None:
            n = notes.get(a_src["source_url"].split("#")[1].split(".")[0], {})
            if other["source_url"] and other["source_url"].split("#")[0] in n.get(
                "source_urls", ""
            ):
                chk["independence"] = f"{a_src['source']} cites {other['source_url']}: counted once"
                continue
        indep.add(
            d["source"]
            + (":" + d["qualifiers"]["via"] if d.get("qualifiers", {}).get("via") else "")
        )
    indep |= {x for x in db if ":" not in x}
    # confidence
    kind = (
        "map"
        if c["source"].startswith("map:")
        else c["source"]
        if c["source"] in ("musicbrainz", "discogs", "wikidata", "firecrawl_json")
        else "text"
    )
    conf = BASE.get((kind, c["basis"]), 0.5) + 0.1 * len(indep)
    if any(x.endswith("contradicts") for x in db):
        fails.append("database contradicts the claim")
    rv = reader.get(c["claim_id"])
    if rv:
        chk["reader"] = rv["verdict"] + ": " + rv["reason"]
        if rv["verdict"] in ("DOES_NOT_SUPPORT", "NOT_A_CLAIM", "WRONG_DIRECTION"):
            fails.append("independent reader: " + rv["verdict"])
    c["checks"], c["independent_support"] = chk, sorted(indep)
    c["confidence"] = 0.0 if fails else round(min(conf, 0.95), 2)
    c["fails"] = fails
    read_ok = (
        kind in ("musicbrainz", "discogs", "wikidata", "firecrawl_json")
        or c["evidence"].startswith("field:")
        or (rv and rv["verdict"] == "SUPPORTS")
    )
    db_ok = any(":" not in x for x in db)
    # accepted = the source says this and every check passed; confidence carries how much weight it gets
    c["suggested_status"] = (
        "rejected"
        if fails
        else "accepted"
        if read_ok or (rv and rv["verdict"] == "PARTIAL" and db_ok)
        else "ask_matt"
        if rv and rv["verdict"] == "PARTIAL"
        else "unread"
    )
with open(f"{run}/claims_verified.jsonl", "w", encoding="utf-8") as f:
    for c in claims:
        f.write(json.dumps(c, ensure_ascii=False) + "\n")

print(Counter(c["suggested_status"] for c in claims))
for c in claims:
    if c.get("extractor") == "claude" or c["fails"]:
        print(
            c["claim_id"].ljust(14),
            c["predicate"].ljust(15),
            c["subject"]["name"][:34].ljust(34),
            "→",
            c["object"]["name"][:34].ljust(34),
            c["confidence"],
            c["suggested_status"],
            "|",
            "; ".join(c["fails"]) or ",".join(c["independent_support"]),
            "|",
            c["checks"].get("mb_work", ""),
            c["checks"].get("independence", ""),
            c["checks"].get("dates", ""),
        )
