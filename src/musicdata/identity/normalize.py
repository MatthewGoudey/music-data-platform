"""Deterministic keys for artist and album names (the unmapped-listen fallback).

When ListenBrainz supplies MusicBrainz IDs, identity comes from the IDs and these
functions are only used for display grouping. For the ~12% of listens without a
mapping, these keys ARE the identity, so the rules are strict and tested:

norm_key(name):
  1. Unicode NFKC, then casefold.
  2. Strip combining marks (diacritics) — this affects Latin scripts (Björk → bjork)
     and leaves CJK untouched; Hangul syllables are re-composed afterwards.
  3. "&" becomes " and ".
  4. Cut at a featured-artist marker (feat. / ft. / featuring), never at "and" or "&".
  5. Dots and apostrophes are removed outright (R.E.M. → rem, I'm → im);
     every other non-letter, non-digit character becomes a space.
  6. Collapse whitespace; drop one leading English "the ".
  7. If the result is empty (e.g. "!!!", "@", "¥$"), fall back to the casefolded,
     whitespace-collapsed raw string. The key is never empty for non-empty input.

album_key(title): the same pipeline, after stripping edition markers:
  - a parenthesised or bracketed group containing an edition word
    (deluxe, remaster, expanded, anniversary, bonus, edition, version, reissue,
    mono, stereo, explicit, clean, complete, legacy) is removed;
  - a trailing " - <...> edition/version/remaster/deluxe" suffix is removed;
  - "EP" is deliberately kept: "Lately EP" and "Lately" are different keys.

title_key(title): the key for a track title, used when a listen has no recording MBID.
  The same edition stripping as album_key ("Heroes - 2017 Remaster" → heroes), then
  the featured-artist cut as in norm_key ("Kiss Me More (feat. SZA)" → kiss me more).
  "with X" stays: it is often part of the official title.
"""

from __future__ import annotations

import re
import unicodedata

_EDITION_WORDS = (
    r"deluxe|remaster(?:ed)?|expanded|anniversary|bonus|special|edition|version|"
    r"reissue|mono|stereo|explicit|clean|complete|legacy"
)
_BRACKET_EDITION = re.compile(
    rf"\s*[\(\[][^\)\]]*\b(?:{_EDITION_WORDS})\b[^\)\]]*[\)\]]",
    re.IGNORECASE,
)
_TRAILING_EDITION = re.compile(
    rf"\s+[-–—]\s+[^-–—]*\b(?:{_EDITION_WORDS})\b[^-–—]*$",
    re.IGNORECASE,
)
_FEAT = re.compile(
    r"(?:\s+|[\(\[])(?:feat\.?|ft\.?|featuring)\s+.*$",
    re.IGNORECASE,
)
_DROP_CHARS = re.compile(r"[.'’ʼ`´]")
_SPACES = re.compile(r"\s+")


def _is_latin(ch: str) -> bool:
    return ch.isascii() and ch.isalpha() or "LATIN" in unicodedata.name(ch, "")


def _strip_marks(s: str) -> str:
    """Drop combining marks only when they sit on a Latin base letter.

    Björk → bjork, but Japanese dakuten (ぶ = ふ + ゛) and Cyrillic breves (й) are
    part of the letter and stay; NFC re-composes them afterwards.
    """
    decomposed = unicodedata.normalize("NFKD", s)
    out: list[str] = []
    prev_latin = False
    for ch in decomposed:
        if unicodedata.category(ch) == "Mn":
            if prev_latin:
                continue
            out.append(ch)
            continue
        out.append(ch)
        prev_latin = _is_latin(ch)
    return unicodedata.normalize("NFC", "".join(out))


def _letters_digits_only(s: str) -> str:
    out: list[str] = []
    for ch in s:
        cat = unicodedata.category(ch)
        out.append(ch if cat[0] in ("L", "N") else " ")
    return "".join(out)


def _collapse(s: str) -> str:
    return _SPACES.sub(" ", s).strip()


def _key(raw: str, *, cut_featured: bool) -> str:
    if raw is None:
        return ""
    s = unicodedata.normalize("NFKC", str(raw)).casefold()
    s = _strip_marks(s)
    s = s.replace("&", " and ")
    if cut_featured:
        s = _FEAT.sub("", s)
    s = _DROP_CHARS.sub("", s)
    s = _letters_digits_only(s)
    s = _collapse(s)
    if s.startswith("the "):
        s = s[4:]
    if not s:
        s = _collapse(unicodedata.normalize("NFKC", str(raw)).casefold())
    return s


def norm_key(name: str) -> str:
    """The artist key. Empty only for empty or whitespace-only input."""
    return _key(name, cut_featured=True)


def _strip_editions(title: str) -> str:
    s = _BRACKET_EDITION.sub("", str(title))
    return _TRAILING_EDITION.sub("", s)


def album_key(title: str) -> str:
    """The album key: edition markers stripped, 'EP' kept."""
    if title is None:
        return ""
    return _key(_strip_editions(title), cut_featured=False)


def title_key(title: str) -> str:
    """The track-title key: edition markers stripped, featured artists cut."""
    if title is None:
        return ""
    return _key(_strip_editions(title), cut_featured=True)


def split_featured(name: str) -> tuple[str, list[str]]:
    """Return (primary artist raw name, [featured raw names]) without normalizing.

    Used at ingest to keep featured artists as separate credits when ListenBrainz
    did not already split them.
    """
    if not name:
        return "", []
    m = re.search(r"(?:\s+|[\(\[])(?:feat\.?|ft\.?|featuring)\s+(.*)$", name, re.IGNORECASE)
    if not m:
        return name.strip(), []
    primary = name[: m.start()].strip()
    rest = m.group(1).strip().rstrip(")]").strip()
    featured = [p.strip() for p in re.split(r",|&|\band\b", rest) if p.strip()]
    return primary, featured
