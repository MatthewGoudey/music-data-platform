"""The free baseline's claims (GRAPH_SPEC 7.1): MusicBrainz and Discogs JSON in, claim specs out.
Pure functions; a port of `scripts/graph/facts_to_claims.py`'s database half, with the same
evidence wording, qualifiers and one-claim-per-person merging.

Every claim here is `documented`, its extractor is its source, and its confidence is 0.9; the
album being researched is `ALBUM`, filled in by the job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from musicdata.clients.discogs import clean_name, clean_role


@dataclass(frozen=True)
class Ent:
    type: str
    name: str
    mbid: str | None = None


ALBUM = Ent("album", "")  # the target album; the job knows its entity id


@dataclass
class ClaimSpec:
    subject: Ent
    predicate: str
    obj: Ent
    qualifiers: dict[str, object]
    source: str  # musicbrainz, discogs
    evidence: str
    source_url: str | None
    attrs: dict[str, object] = field(default_factory=dict)

    @property
    def extractor(self) -> str:
        return self.source


DOMAIN_KINDS = {
    "wikipedia.org": "wikipedia",
    "wikidata.org": "wikidata",
    "discogs.com": "discogs",
    "allmusic.com": "allmusic",
    "bandcamp.com": "bandcamp",
    "musicbrainz.org": "musicbrainz",
}
STREAMING = ("spotify.com", "apple.com", "deezer.com", "tidal.com", "youtube.com",
             "soundcloud.com", "qobuz.com", "napster.com", "amazon.")  # fmt: skip
REL_KINDS = {"official homepage": "official", "review": "review", "interview": "interview"}


def link_kind(url: str, rel_type: str | None = None) -> str:
    host = (urlparse(url).hostname or "").lower()
    for domain, kind in DOMAIN_KINDS.items():
        if host == domain or host.endswith("." + domain):
            return kind
    if any(s in host for s in STREAMING):
        return "streaming"
    return REL_KINDS.get(rel_type or "", "other")


def url_links(entity_json: dict) -> list[tuple[str, str]]:
    """(kind, url) for every URL relation of a MusicBrainz entity."""
    out = []
    for rel in entity_json.get("relations") or []:
        url = (rel.get("url") or {}).get("resource")
        if url:
            out.append((link_kind(url, rel.get("type")), url))
    return out


def _artist_ent(a: dict) -> Ent:
    return Ent("person" if a.get("type") == "Person" else "artist", a["name"], a["id"])


def role_of(rel: dict) -> list[str]:
    attrs = [a for a in rel.get("attributes") or [] if a not in ("guest", "additional")]
    if rel["type"] in ("instrument", "vocal"):
        return attrs or [rel["type"]]
    return [("co-" if "co" in attrs else "") + rel["type"]]


def musicbrainz_claims(release: dict, artists: list[dict]) -> list[ClaimSpec]:
    """Credits, songwriting, places and labels from the canonical release; memberships and
    areas from the album artists."""
    url = f"https://musicbrainz.org/release/{release['id']}"
    claims: list[ClaimSpec] = []
    people: dict[str, dict] = {}
    places: list[dict] = []
    writers: dict[str, dict] = {}

    def take(rels: list[dict], track: str | None) -> None:
        for rel in rels:
            if rel.get("target-type") == "artist":
                p = people.setdefault(
                    rel["artist"]["id"],
                    {"ent": _artist_ent(rel["artist"]), "roles": set(), "tracks": set()},
                )
                p["roles"].update(role_of(rel))
                if track:
                    p["tracks"].add(track)
            elif rel.get("target-type") == "place":
                places.append(
                    {"place": rel["place"], "role": rel["type"], "begin": rel.get("begin")}
                )

    take(release.get("relations") or [], None)
    for medium in release.get("media") or []:
        for t in medium.get("tracks") or []:
            rec = t.get("recording") or {}
            take(rec.get("relations") or [], t.get("title"))
            for rel in rec.get("relations") or []:
                if rel.get("target-type") != "work":
                    continue
                for wr in (rel.get("work") or {}).get("relations") or []:
                    if wr.get("target-type") == "artist" and wr["type"] in (
                        "composer", "lyricist", "writer",
                    ):  # fmt: skip
                        w = writers.setdefault(
                            wr["artist"]["id"], {"ent": _artist_ent(wr["artist"]), "tracks": []}
                        )
                        if t.get("title") not in w["tracks"]:
                            w["tracks"].append(t.get("title"))

    for p in people.values():
        roles = sorted(p["roles"])
        claims.append(
            ClaimSpec(
                p["ent"], "credited_on", ALBUM,
                {"role": roles, "tracks": len(p["tracks"]) or "album"},
                "musicbrainz",
                f"MusicBrainz release credits: {p['ent'].name} — {', '.join(roles)}",
                url,
            )
        )  # fmt: skip
    for w in writers.values():
        claims.append(
            ClaimSpec(
                w["ent"], "credited_on", ALBUM,
                {"role": ["songwriter"], "tracks": w["tracks"]},
                "musicbrainz",
                f"MusicBrainz works written by {w['ent'].name}: {', '.join(w['tracks'])}",
                url,
            )
        )  # fmt: skip
    seen: set[tuple[str, str]] = set()
    for pl in places:
        k = (pl["place"]["id"], pl["role"])
        if k in seen:
            continue
        seen.add(k)
        claims.append(
            ClaimSpec(
                ALBUM, "recorded_at", Ent("place", pl["place"]["name"], pl["place"]["id"]),
                {"mb_role": pl["role"], "dates": pl["begin"]},
                "musicbrainz", f"MusicBrainz: {pl['role']} {pl['place']['name']}", url,
            )
        )  # fmt: skip
    for li in release.get("label-info") or []:
        label = li.get("label")
        if not label:
            continue
        catalog = li.get("catalog-number")
        claims.append(
            ClaimSpec(
                ALBUM, "released_by", Ent("label", label["name"], label["id"]),
                {"catalog": catalog}, "musicbrainz",
                f"MusicBrainz label info: {label['name']} {catalog or ''}".strip(), url,
            )
        )  # fmt: skip
    for a in artists:
        claims += artist_claims(a)
    return claims


def artist_claims(a: dict) -> list[ClaimSpec]:
    """`member_of` (both directions) and `based_in` for one album artist."""
    url = f"https://musicbrainz.org/artist/{a['id']}"
    me = _artist_ent(a)
    out: list[ClaimSpec] = []
    done: set[str] = set()
    for r in a.get("relations") or []:
        if r.get("target-type") != "artist" or r["type"] != "member of band":
            continue
        other = _artist_ent(r["artist"])
        if other.mbid in done:
            continue
        done.add(other.mbid)
        person, group = (me, other) if r.get("direction") == "forward" else (other, me)
        if person.type != "person":
            person = Ent("person", person.name, person.mbid)
        out.append(
            ClaimSpec(
                person, "member_of", group,
                {"from": r.get("begin"), "to": r.get("end"), "instrument": r.get("attributes") or []},
                "musicbrainz", f"MusicBrainz: {person.name} member of band {group.name}", url,
            )
        )  # fmt: skip
    area = a.get("begin-area") or a.get("area")
    if area:
        out.append(
            ClaimSpec(
                me, "based_in", Ent("area", area["name"], area.get("id")), {}, "musicbrainz",
                f"MusicBrainz area: {(a.get('area') or {}).get('name')}; "
                f"begin area: {(a.get('begin-area') or {}).get('name')}",
                url,
            )
        )  # fmt: skip
    return out


def discogs_claims(dg: dict) -> list[ClaimSpec]:
    """One `credited_on` per person, `released_by` per label, `has_genre` per style."""
    out: list[ClaimSpec] = []
    people: dict[str, set[str]] = {}
    for c in dg.get("credits") or []:
        people.setdefault(clean_name(c["name"]), set()).add(clean_role(c.get("role") or ""))
    for name, roles in people.items():
        r = sorted(x for x in roles if x)
        out.append(
            ClaimSpec(
                Ent("person", name), "credited_on", ALBUM, {"role": r}, "discogs",
                f"Discogs credits: {name} — {', '.join(r)}", dg["url"],
            )
        )  # fmt: skip
    for ln in dg.get("labels") or []:
        name = clean_name(ln["name"])
        out.append(
            ClaimSpec(
                ALBUM, "released_by", Ent("label", name), {"catalog": ln.get("catno")}, "discogs",
                f"Discogs label: {ln['name']} {ln.get('catno') or ''}".strip(), dg["url"],
            )
        )  # fmt: skip
    for st in dg.get("styles") or []:
        out.append(
            ClaimSpec(
                ALBUM, "has_genre", Ent("genre", st), {"vocabulary": "discogs_style"}, "discogs",
                f"Discogs style: {st}", dg["url"],
            )
        )  # fmt: skip
    return out
