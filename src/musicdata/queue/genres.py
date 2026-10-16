"""Genres (queue spec v23): one vocabulary over the list genre fields (RYM, Acclaimed Music, 1001
Albums, Rolling Stone) and the graph's `has_genre` claims (Discogs styles).

The sources spell one genre many ways ("singer-songwriter", "singer/songwriter"; "bossanova",
"bossa nova"; "rock & roll", "rock and roll"), one list pairs a Discogs genre with its style
("rock; classic rock"), and some files lost their accents ("musique concr�te"). A genre's identity
is its key: the cleaned name after `genre_aliases.csv`, without accents, spaces or
punctuation, "&" read as "and". Its name is the best spelling seen: the most word breaks, then
accents, then the most common.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from collections import Counter, defaultdict
from functools import cache
from pathlib import Path

import asyncpg

ALIASES_CSV = Path(__file__).with_name(
    "genre_aliases.csv"
)  # in the package: the image has no seeds/
# Discogs top-level groups that name no style, fragments of "Folk, World, & Country", and Discogs
# styles that describe a record rather than place it
DROP = {"none", "& country", "stage & screen", "non-music", "children's", "brass & military",
        "ballad", "acoustic", "dialogue", "score"}  # fmt: skip
LOST = re.compile(r"ï¿½|\?|�")  # a character a file lost, written one way for the aliases
ACRONYMS = {"uk", "us", "idm", "edm", "ebm", "aor", "r&b", "rnb", "dj", "ecm", "nyc", "la"}

RAW_GENRES = r"""
SELECT e.release_group_id AS rg, e.facets ->> 'genre' AS raw
  FROM list_entry e
 WHERE e.facets ? 'genre' AND e.release_group_id IS NOT NULL
   AND ($1::int[] IS NULL OR e.release_group_id = ANY($1::int[]))
"""
CLAIM_GENRES = """
SELECT en.release_group_id AS rg, o.name AS raw
  FROM assertion a
  JOIN entity en ON en.entity_id = a.subject_id AND en.type = 'album'
  JOIN entity o ON o.entity_id = a.object_id
 WHERE a.predicate = 'has_genre' AND a.status = 'accepted' AND en.release_group_id IS NOT NULL
   AND ($1::int[] IS NULL OR en.release_group_id = ANY($1::int[]))
"""


@cache
def aliases() -> dict[str, str]:
    """Cleaned spelling -> the name it stands for."""
    if not ALIASES_CSV.exists():
        return {}
    with ALIASES_CSV.open(encoding="utf-8", newline="") as f:
        return {_clean(r["alias"]): _clean(r["genre"]) for r in csv.DictReader(f)}


def _clean(part: str) -> str:
    # lost characters first: NFKC would turn the "½" of "ï¿½" into "1⁄2"
    s = unicodedata.normalize("NFKC", LOST.sub("�", part)).replace("\xa0", " ")
    return " ".join(s.lower().split())


def genre_key(name: str) -> str:
    s = unicodedata.normalize("NFKD", name.replace("&", " and "))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def split_genres(raw: str | None) -> list[str]:
    """A source's genre text -> cleaned names. Bracketed notes ("jazz (specifically vocal
    jazz, …)", "trap[edm]") go before the split on commas and semicolons."""
    if not raw:
        return []
    text = re.sub(r"\s*\([^)]*\)|\s*\[[^\]]*\]", "", raw)
    out = []
    for part in re.split(r"[,;]", text):
        name = _clean(part)
        name = aliases().get(name, name)
        if name and name not in DROP and "�" not in name and genre_key(name):
            out.append(name)
    return out


def key_of(name: str) -> str:
    """The key of one genre name as a source spells it, aliases applied ("Glam" -> glamrock)."""
    names = split_genres(name)
    return genre_key(names[0]) if names else genre_key(name)


def _rank(name: str, count: int) -> tuple[int, int, int]:
    return (name.count(" "), sum(not c.isascii() for c in name), count)


def best_names(spellings: dict[str, Counter[str]]) -> dict[str, str]:
    """key -> the spelling to show; an alias target always wins."""
    targets = set(aliases().values())
    out = {}
    for key, seen in spellings.items():
        forced = [n for n in seen if n in targets]
        out[key] = forced[0] if forced else max(seen, key=lambda n: _rank(n, seen[n]))
    return out


def title(name: str) -> str:
    """Sentence case for a chip or a heading, acronyms kept: "UK bass", "Musique concrète"."""
    words = [w.upper() if w in ACRONYMS else w for w in name.split()]
    if words and words[0] not in {w.upper() for w in ACRONYMS}:
        words[0] = words[0][:1].upper() + words[0][1:]
    return " ".join(words)


async def raw_rows(conn: asyncpg.Connection, rgs: list[int] | None) -> list[tuple[int, str]]:
    rows = [(r["rg"], r["raw"]) for r in await conn.fetch(RAW_GENRES, rgs)]
    if await conn.fetchval("SELECT to_regclass('assertion') IS NOT NULL"):
        rows += [(r["rg"], r["raw"]) for r in await conn.fetch(CLAIM_GENRES, rgs)]
    return rows


def index_rows(rows: list[tuple[int, str]]) -> tuple[dict[int, set[str]], dict[str, str]]:
    """(release group -> genre keys, key -> name)."""
    by_rg: dict[int, set[str]] = defaultdict(set)
    spellings: dict[str, Counter[str]] = defaultdict(Counter)
    for rg, raw in rows:
        for name in split_genres(raw):
            key = genre_key(name)
            by_rg[rg].add(key)
            spellings[key][name] += 1
    return by_rg, best_names(spellings)


async def album_genres(conn: asyncpg.Connection, rgs: list[int]) -> dict[int, dict[str, str]]:
    """Each album's genres as key -> name."""
    if not rgs:
        return {}
    by_rg, names = index_rows(await raw_rows(conn, rgs))
    return {rg: {k: names[k] for k in keys} for rg, keys in by_rg.items()}
