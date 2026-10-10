"""Verification (GRAPH_SPEC 8): the checks that need no music knowledge. A port of
`scripts/graph/verify.py` that keeps every rule.

| Check | Fails when |
| --- | --- |
| Structure | unknown predicate; subject or object type outside it; `sounds_like` not inferred; `influenced_by` neither documented nor reported; a `claude` lineage claim without `direction = subject_newer` |
| Evidence | a text quote (split on " … ") missing from the normalised page or atlas note; a `field:` value missing from its page; a Firecrawl-JSON name missing from its page; evidence over 300 characters |
| Dates | lineage: the object's year is later than the subject's |
| First recording | `covers`: an earlier recording of the work by another artist than the object's |
| Databases | MusicBrainz contradicts a cover's order (MusicBrainz or Discogs agreeing adds support) |
| Reader | `DOES_NOT_SUPPORT`, `WRONG_DIRECTION` or `NOT_A_CLAIM` |

`verify()` is pure: claims are dicts (subject and object as `{type, name, year?, artist?,
atlas_id?}`), and everything looked up elsewhere arrives in a `Lookups`. The job in
`graph/verify_job.py` builds both from the database.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

ART = {"artist", "person"}
PRED: dict[str, tuple[set[str], set[str]]] = {
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
    "toured_with": (ART, ART),
    "performs_as": (ART, ART),
    "renamed_from": (ART, ART),
    "interpolates": ({"recording", "album"}, {"recording", "work"}),
    "path_next": ({"album"}, {"album"}),
    "has_tag": ({"album"}, {"tag"}),
    "on_list": ({"album"}, {"list"}),
}
LINEAGE = {"influenced_by", "sounds_like", "covers", "samples", "interpolates"}
DB = ("musicbrainz", "discogs", "wikidata")
BASE = {
    ("musicbrainz", "documented"): 0.9,
    ("discogs", "documented"): 0.9,
    ("wikidata", "documented"): 0.9,
    ("firecrawl_json", "documented"): 0.75,
    ("map", "reported"): 0.7,
    ("map", "inferred"): 0.5,  # the atlas is AI-written (spec v4)
    ("text", "documented"): 0.8,
    ("text", "reported"): 0.7,
    ("text", "inferred"): 0.5,
}
CAP = 0.95
EVIDENCE_MAX = 300
REJECTING = ("DOES_NOT_SUPPORT", "NOT_A_CLAIM", "WRONG_DIRECTION")

# a link target, with nested parentheses and an optional "title" (which may hold escaped \"
# quotes: "George \"Chocolate\" Perry")
_URL = r"\((?:[^()\s]|\([^()\s]*\))*(?:\s+\"(?:[^\"\\]|\\.)*\")?\)"
_CITE_LINK = re.compile(r"\[\\?\[\d+\\?\]\]" + _URL)  # [\[4\]](…#cite_note-4)
_LINK = re.compile(r"\[([^\]]*)\]" + _URL)
_TITLE_REMNANT = re.compile(r' "[^"]+"\)')
_CITE = re.compile(r"\[\d+\]")
_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "_": None, "*": None})


def norm(text: str | None) -> str:
    """Page text and quotes made comparable: markdown links to their text, citation markers
    and link-title remnants dropped, quotes unified, emphasis and underscores dropped,
    whitespace collapsed, lower case."""
    s = _CITE_LINK.sub("", text or "")
    s = _LINK.sub(r"\1", s)
    s = _TITLE_REMNANT.sub("", s)
    s = _CITE.sub("", s)
    return re.sub(r"\s+", " ", s.translate(_QUOTES)).strip().lower()


def match_key(name: str | None) -> str:
    """Names made comparable across sources: accents, case, apostrophes and punctuation
    dropped, `&` read as `and`."""
    s = unicodedata.normalize("NFKD", (name or "").replace("'", "").replace("’", "")).lower()
    s = "".join(c for c in s if not unicodedata.combining(c)).replace("&", "and")
    return re.sub(r"[^\w]+", " ", s).strip()


def same_name(a: str | None, b: str | None) -> bool:
    """Equal keys, or the shorter key (5 characters or more) inside the longer one
    ("Wally Heider" and "Wally Heider Studios")."""
    ka, kb = match_key(a), match_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    short, long_ = sorted((ka, kb), key=len)
    return len(short) >= 5 and short in long_


_QUOTE_MARKS = str.maketrans({'"': None, "'": None})
NOT_PLACES = {"various", "several", "multiple", "unknown", "other"}  # "Various studios"
_STUDIO_SUFFIX = re.compile(r"\s+(recording\s+)?studios?(\s+\w+)?$")


def name_on_page(name: str, page: str, *, place: bool = False) -> bool:
    """An extracted name is on its (normalised) page, quote marks aside (`"Sneaky" Pete
    Kleinow`); a place also passes without a trailing "Studio(s)" word ("Wally Heider" for
    "Wally Heider Studios"), when 5 or more characters remain (spec change v2)."""
    hay = page.translate(_QUOTE_MARKS)
    nm = norm(name).translate(_QUOTE_MARKS).strip()
    if nm and nm in hay:
        return True
    if place:
        short = _STUDIO_SUFFIX.sub("", nm)
        return short != nm and len(short) >= 5 and short not in NOT_PLACES and short in hay
    return False


@dataclass
class Lookups:
    pages: dict[str, str] = field(default_factory=dict)  # url -> norm(body)
    notes: dict[str, dict] = field(
        default_factory=dict
    )  # atlas_id -> description, lineage, source_urls
    works: dict[str, dict] = field(
        default_factory=dict
    )  # match_key(title) -> work and its recordings
    artists: dict[str, dict] = field(default_factory=dict)  # match_key(name) -> name, relations
    foreign: dict[str, set[str]] = field(
        default_factory=dict
    )  # album name -> {"musicbrainz": keys, ...}
    years: dict[tuple[str, str], int] = field(default_factory=dict)  # (type, name) -> year
    album_years: dict[str, int] = field(default_factory=dict)  # atlas_id -> first release year
    reader: dict[str, dict] = field(default_factory=dict)  # claim label -> verdict, reason


def kind(c: dict) -> str:
    """musicbrainz, discogs, wikidata, firecrawl_json, matt, map or text."""
    ex = c.get("extractor") or c["source"]
    if ex in DB or ex in ("firecrawl_json", "matt"):
        return ex
    if c["source"] in DB:
        return c["source"]
    return "map" if c["source"].startswith("map:") else "text"


def _note_id(url: str | None) -> str:
    """`atlas:seeds/atlas/album_notes.csv#A0015.lineage` -> A0015."""
    return (url or "").split("#", 1)[-1].split(".")[0]


def _work_title(name: str) -> str:
    return name.split(" – ")[-1].split("(")[0]


def year_of(e: dict, lk: Lookups) -> int | None:
    if e.get("year"):
        return int(str(e["year"])[:4])
    if e["type"] in ("album", *ART):
        name = e["name"].split(" – ", 1)[-1] if e["type"] == "album" else e["name"]
        return lk.years.get((e["type"], name))
    return None


def _structure(c: dict) -> list[str]:
    s, p, o = c["subject"], c["predicate"], c["object"]
    fails = []
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
    return fails


def evidence_check(c: dict, k: str, lk: Lookups, chk: dict) -> list[str]:
    fails = []
    url = c.get("source_url") or ""
    if k == "firecrawl_json":  # extraction can invent: the name must be on its page
        place = c["predicate"] == "recorded_at"
        ent = c["object"] if place else c["subject"]
        page = lk.pages.get(url, "")
        # an entity merged by the graph spec v10 rule keeps its merged names ("Sound Shop" in
        # "The Sound Shop")
        found = any(
            name_on_page(n, page, place=place) for n in [ent["name"], *ent.get("aliases", [])]
        )
        chk["name_on_page"] = "pass" if found else "fail"
        if chk["name_on_page"] == "fail":
            fails.append("extracted name not on the cached page")
    if c["evidence"].startswith("field:"):
        val = norm(c["evidence"].split("=", 1)[-1])
        here = lk.pages.get(url.split("#")[0], "") + lk.pages.get(url, "")
        found = bool(val) and (
            val in here or any(val in v for u, v in lk.pages.items() if url and u.startswith(url))
        )
        chk["evidence_field"] = "pass" if found else "fail"
        if not found:
            fails.append("field value not on the cached page")
    elif k in ("text", "map"):
        if k == "map":
            n = lk.notes.get(_note_id(url), {})
            hay = norm((n.get("description") or "") + " " + (n.get("lineage") or ""))
        else:
            hay = lk.pages.get(url, "")
        segs = [x.strip() for x in norm(c["evidence"]).split("…") if x.strip()]
        chk["evidence_verbatim"] = "pass" if segs and all(x in hay for x in segs) else "fail"
        if chk["evidence_verbatim"] == "fail":
            fails.append("evidence not found verbatim in the cached source")
        if len(c["evidence"]) > EVIDENCE_MAX:
            fails.append("evidence over 300 characters")
    return fails


def _dates(c: dict, lk: Lookups, chk: dict) -> list[str]:
    def year(e: dict) -> int | None:
        y = year_of(e, lk)
        if y is None and e.get("atlas_id"):
            y = lk.album_years.get(c.get("album") or "")
        return y

    sy, oy = year(c["subject"]), year(c["object"])
    chk["dates"] = {"subject": sy, "object": oy}
    if sy and oy:
        chk["direction"] = "pass" if oy <= sy else "fail"
        if oy > sy:
            return [f"direction: object ({oy}) is newer than subject ({sy})"]
    else:
        chk["direction"] = "unknown"
    return []


def _covers(c: dict, lk: Lookups, chk: dict, db: list[str]) -> list[str]:
    s, o = c["subject"], c["object"]
    w = lk.works.get(match_key(_work_title(o.get("name", ""))))
    if not w:
        return []
    fails = []
    recs = {match_key(r["artist"]): r for r in w["sample"]}
    rs, ro = recs.get(match_key(s.get("artist", ""))), recs.get(match_key(o.get("artist", "")))
    dated = [r for r in w["sample"] if r["first_release"]]
    earliest = min(dated, key=lambda r: r["first_release"]) if dated else None
    if (
        earliest
        and ro
        and match_key(earliest["artist"]) != match_key(ro["artist"])
        and earliest["first_release"][:4] < (ro["first_release"] or "9999")[:4]
    ):
        fails.append(
            f"object is not the first recording: {earliest['artist']} "
            f"({earliest['first_release']}) is earlier"
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
            s.get("artist", "") if not rs else o.get("artist", "")
        )
    return fails


def _databases(c: dict, lk: Lookups, chk: dict, db: list[str]) -> None:
    s, p, o = c["subject"], c["predicate"], c["object"]
    if p == "member_of":
        a = lk.artists.get(match_key(o["name"])) or lk.artists.get(match_key(s["name"]))
        if a:
            other = s["name"] if match_key(a["name"]) == match_key(o["name"]) else o["name"]
            hit = any(
                r["type"] == "member of band" and match_key(r["other"]) == match_key(other)
                for r in a["relations"]
            )
            db.append("musicbrainz" if hit else "musicbrainz:absent")
    if p == "credited_on" and o["name"] in lk.foreign:
        fb = lk.foreign[o["name"]]
        for src in ("musicbrainz", "discogs"):
            if match_key(s["name"]) in fb.get(src, set()):
                db.append(src)
        chk["foreign_album"] = fb.get("status")
    if c["source"] not in DB and p in ("credited_on", "member_of", "released_by", "recorded_at"):
        db += [x for x in c.get("corroborated_by") or [] if x in ("musicbrainz", "discogs")]


def _edges(claims: list[dict]) -> dict[str, list[dict]]:
    by_pred: dict[str, list[dict]] = defaultdict(list)
    for c in claims:
        by_pred[c["predicate"]].append(c)
    return by_pred


def _independent(c: dict, by_pred: dict[str, list[dict]], lk: Lookups, chk: dict) -> set[str]:
    """Distinct origins that agree; an atlas note built from the same page counts once."""
    s, o = c["subject"], c["object"]
    indep = set(c.get("corroborated_by") or [])
    for d in by_pred[c["predicate"]]:
        if d is c or d["source"] == c["source"]:
            continue
        if not (
            same_name(d["subject"]["name"], s["name"]) and same_name(d["object"]["name"], o["name"])
        ):
            continue
        atlas = (
            c if c["source"].startswith("map:") else d if d["source"].startswith("map:") else None
        )
        if atlas is not None:
            other = d if atlas is c else c
            cited = lk.notes.get(_note_id(atlas.get("source_url")), {}).get("source_urls") or ""
            other_url = (other.get("source_url") or "").split("#")[0]
            if other_url and other_url in cited:
                chk["independence"] = f"{atlas['source']} cites {other['source_url']}: counted once"
                continue
        via = (d.get("qualifiers") or {}).get("via")
        indep.add(d["source"] + (":" + via if via else ""))
    return indep


def verify(claims: list[dict], lk: Lookups) -> list[dict]:
    """Every claim gets `checks`, `fails`, `independent_support`, `confidence` and
    `suggested_status` (rejected, accepted, ask_matt or unread)."""
    by_pred = _edges(claims)
    for c in claims:
        chk: dict = {}
        k = kind(c)
        fails = _structure(c)
        chk["structure"] = "pass" if not fails else "fail"
        if k != "matt":
            fails += evidence_check(c, k, lk, chk)
        if c["predicate"] in LINEAGE:
            fails += _dates(c, lk, chk)
        db: list[str] = []
        if c["predicate"] == "covers":
            fails += _covers(c, lk, chk, db)
        _databases(c, lk, chk, db)
        indep = _independent(c, by_pred, lk, chk) | {x for x in db if ":" not in x}
        if any(x.endswith("contradicts") for x in db):
            fails.append("database contradicts the claim")
        rv = lk.reader.get(c["claim_id"])
        if rv:
            chk["reader"] = rv["verdict"] + ": " + (rv.get("reason") or "")
            if rv["verdict"] in REJECTING:
                fails.append("independent reader: " + rv["verdict"])
        conf = 1.0 if k == "matt" else min(BASE.get((k, c["basis"]), 0.5) + 0.1 * len(indep), CAP)
        read_ok = (
            k in (*DB, "firecrawl_json", "matt")
            or c["evidence"].startswith("field:")
            or bool(rv and rv["verdict"] == "SUPPORTS")
        )
        partial = bool(rv and rv["verdict"] == "PARTIAL")
        db_ok = any(":" not in x for x in db)
        c["checks"], c["independent_support"], c["fails"] = chk, sorted(indep), fails
        c["confidence"] = 0.0 if fails else round(conf, 2)
        c["suggested_status"] = (
            "rejected"
            if fails
            else "accepted"
            if read_ok or (partial and db_ok)
            else "ask_matt"
            if partial
            else "unread"
        )
    return claims
