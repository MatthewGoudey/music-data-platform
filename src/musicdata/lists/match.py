"""Choosing a release group for a list entry (docs/QUEUE_SPEC.md section 5). Pure functions.

A MusicBrainz search hit becomes a `Candidate` when its artist credit names the entry's
artist and its title has the entry's album key (or nearly: "pt3" ~ "part 3"). Among the
candidates, each rule narrows only when it leaves someone standing:

1. the exact album key over a near one;
2. a first-release year within ±1 of the entry's year;
3. a plain album over one with secondary types (live, compilation, soundtrack);
4. Album over EP.

One left → resolved; several → ambiguous (kept for review); none → unresolved.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass

from musicdata.identity import album_key, norm_key, title_key
from musicdata.resolve.unmapped import titles_match

TYPES = ("Album", "EP")


@dataclass(frozen=True)
class Candidate:
    mbid: str
    title: str
    year: int | None
    primary_type: str | None
    secondary_types: tuple[str, ...]
    artist_mbid: str | None
    artist_name: str | None
    exact: bool = True

    def detail(self) -> dict[str, object]:
        d = asdict(self)
        d["secondary_types"] = list(self.secondary_types)
        return d


def group_key(title: str) -> str:
    """A release group's key from its title, as resolve stores it."""
    return album_key(title) or title_key(title) or "untitled"


def credit_matches(credits: list[dict], artist_key: str) -> bool:
    """The credit names the artist: the whole credit, one credited artist, or the credit
    led by the artist ("Prince and the Revolution" for "Prince")."""
    phrase = norm_key("".join(c.get("name", "") + c.get("joinphrase", "") for c in credits))
    if phrase == artist_key or phrase.startswith(artist_key + " "):
        return True
    names = {c.get("name", "") for c in credits}
    names |= {(c.get("artist") or {}).get(f, "") for c in credits for f in ("name", "sort-name")}
    return any(norm_key(n) == artist_key for n in names if n)


def candidate(hit: dict, artist_key: str, key: str) -> Candidate | None:
    """A search hit that could be the entry's album, or None."""
    if hit.get("primary-type") not in TYPES:
        return None
    if not credit_matches(hit.get("artist-credit") or [], artist_key):
        return None
    hit_key = group_key(hit.get("title", ""))
    if hit_key != key and not titles_match(hit_key, key):
        return None
    first = (hit.get("first-release-date") or "")[:4]
    credit = (hit.get("artist-credit") or [{}])[0]
    return Candidate(
        mbid=hit["id"],
        title=hit.get("title", ""),
        year=int(first) if first.isdigit() else None,
        primary_type=hit.get("primary-type"),
        secondary_types=tuple(hit.get("secondary-types") or ()),
        artist_mbid=(credit.get("artist") or {}).get("id"),
        artist_name=credit.get("name") or (credit.get("artist") or {}).get("name"),
        exact=hit_key == key,
    )


def near_year(a: int | None, b: int | None) -> bool:
    return a is None or b is None or abs(a - b) <= 1


def _narrow(cands: list[Candidate], keep: Callable[[Candidate], bool]) -> list[Candidate]:
    kept = [c for c in cands if keep(c)]
    return kept or cands


def choose(cands: list[Candidate], year: int | None) -> tuple[str, list[Candidate]]:
    """(status, candidates): ("resolved", [winner]), ("ambiguous", tied), ("unresolved", [])."""
    cands = list({c.mbid: c for c in cands}.values())
    if not cands:
        return "unresolved", []
    cands = _narrow(cands, lambda c: c.exact)
    if year is not None:
        cands = _narrow(cands, lambda c: c.year is not None and abs(c.year - year) <= 1)
    cands = _narrow(cands, lambda c: not c.secondary_types)
    cands = _narrow(cands, lambda c: c.primary_type == "Album")
    return ("resolved" if len(cands) == 1 else "ambiguous"), cands
