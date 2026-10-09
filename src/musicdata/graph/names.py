"""MusicBrainz answers turned into what name resolution (GRAPH_SPEC 7.5) and verification need.
Pure functions; ports of `scripts/graph/mb_resolve.py`, `mb_work.py` and `mb_artist.py`.
"""

from __future__ import annotations

import re

from musicdata.graph.verify import match_key

MEMBER_RELATIONS = ("member of band", "collaboration", "is person", "subgroup",
                    "supporting musician", "vocal supporting musician",
                    "instrumental supporting musician")  # fmt: skip
_JOINT = re.compile(r"\s+(?:&|and|with)\s+")


def year(date: str | None) -> int | None:
    return int(date[:4]) if date and date[:4].isdigit() else None


def credit_name(credits: list[dict] | None) -> str:
    return "".join(c.get("name", "") + c.get("joinphrase", "") for c in credits or [])


def exact_artists(results: list[dict], name: str) -> list[dict]:
    """Search hits whose name or an alias equals the name (by match key)."""
    k = match_key(name)
    return [
        r
        for r in results
        if match_key(r.get("name")) == k
        or any(match_key(a.get("name")) == k for a in r.get("aliases") or [])
    ]


def artist_attrs(a: dict) -> dict:
    return {
        "year": year((a.get("life-span") or {}).get("begin")),
        "mb_name": a.get("name"),
        "mb_type": a.get("type"),
        "area": (a.get("area") or {}).get("name"),
        "disambiguation": a.get("disambiguation") or None,
    }


def joint_parts(name: str) -> list[str]:
    """ "Neil Young & Crazy Horse" -> ["Neil Young", "Crazy Horse"]; one name -> []."""
    parts = [x.strip() for x in _JOINT.split(name) if x.strip()]
    return parts if len(parts) > 1 else []


def exact_release_group(results: list[dict], title: str) -> list[dict]:
    k = match_key(title)
    return [r for r in results if match_key(r.get("title")) == k]


def relations(a: dict) -> list[dict]:
    """An artist's band relations, as the member_of cross-check reads them."""
    return [
        {"type": r["type"], "other": r["artist"]["name"], "other_mbid": r["artist"]["id"]}
        for r in a.get("relations") or []
        if r.get("target-type") == "artist" and r.get("type") in MEMBER_RELATIONS
    ]


def work_sample(work: dict, recordings: list[dict], limit: int = 40) -> dict:
    """A work's recordings, one per artist (their earliest), oldest first, with the `cover`
    attribute from the work's relations."""
    attrs = {
        r["recording"]["id"]: r.get("attributes") or []
        for r in work.get("relations") or []
        if r.get("target-type") == "recording"
    }
    rows = [
        {
            "recording": rec["id"],
            "title": rec.get("title"),
            "artist": credit_name(rec.get("artist-credit")),
            "first_release": rec.get("first-release-date") or None,
            "cover": "cover" in attrs.get(rec["id"], []),
            "live": "live" in attrs.get(rec["id"], []),
        }
        for rec in recordings
    ]
    first: dict[str, dict] = {}
    for r in sorted(rows, key=lambda x: x["first_release"] or "9999"):
        first.setdefault(r["artist"], r)
    return {
        "work": work.get("id"),
        "title": work.get("title"),
        "writers": sorted(
            {
                r["artist"]["name"]
                for r in work.get("relations") or []
                if r.get("target-type") == "artist"
            }
        ),
        "recordings": len(first),
        "sample": list(first.values())[:limit],
    }


def release_works(release: dict) -> list[dict]:
    """(track title, work title, work MBID) for every track of a release with a work."""
    out = []
    for medium in release.get("media") or []:
        for track in medium.get("tracks") or []:
            rec = track.get("recording") or {}
            for rel in rec.get("relations") or []:
                if rel.get("target-type") == "work" and rel.get("work"):
                    out.append(
                        {
                            "track": track.get("title") or rec.get("title"),
                            "work": rel["work"].get("title"),
                            "work_mbid": rel["work"]["id"],
                        }
                    )
    return out


def find_work(works: list[dict], title: str) -> dict | None:
    t = match_key(title.split("(")[0])
    return next(
        (
            w
            for w in works
            if match_key(w["track"]) == match_key(title) or match_key(w["work"].split("(")[0]) == t
        ),
        None,
    )


def release_credit_names(release: dict) -> set[str]:
    """Match keys of everyone a release credits: artist credit, release and recording
    relations (the foreign-album cross-check for `credited_on`)."""
    names = {match_key(c.get("name")) for c in release.get("artist-credit") or []}

    def take(rels: list[dict]) -> None:
        for r in rels or []:
            if r.get("target-type") == "artist":
                names.add(match_key(r["artist"]["name"]))

    take(release.get("relations"))
    for medium in release.get("media") or []:
        for track in medium.get("tracks") or []:
            names.update(match_key(c.get("name")) for c in track.get("artist-credit") or [])
            take((track.get("recording") or {}).get("relations"))
    names.discard("")
    return names
