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
  - "EP" is deliberately kept: "Lately EP" and "Lately" are different keys;
  - a re-recording named for its artist ("Red (Taylor's Version)") is a different album,
    not an edition, so "<name>'s Version" stays in the key.

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


_REMAKE = re.compile(r"\b\w+['’]s\s+version\b", re.IGNORECASE)


def _keep_remakes(match: re.Match[str]) -> str:
    return match.group(0) if _REMAKE.search(match.group(0)) else ""


def _strip_editions(title: str) -> str:
    s = _BRACKET_EDITION.sub(_keep_remakes, str(title))
    return _TRAILING_EDITION.sub(_keep_remakes, s)


def strip_edition_markers(title: str) -> str:
    """The raw title without bracketed or dash-suffixed edition markers, for searching
    ("Rumours (Super Deluxe)" → "Rumours"). Keys use the same stripping."""
    return _strip_editions(title).strip()


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


def primary_artist(
    artist_name: str, artist_names: list[str] | None = None, release_artist: str | None = None
) -> str:
    """The raw name of the first credited artist of an unmapped listen.

    Players like Spotify join a track's artists with commas ("Kendrick Lamar, U2"), and
    commas also live inside real names ("Tyler, The Creator"), so a comma alone never
    splits. In order: the submitted artist list's first entry; the album artist when
    the name starts with it and a comma; otherwise the name before any "feat.".
    """
    names = [n.strip() for n in artist_names or [] if n and n.strip()]
    if names:
        return split_featured(names[0])[0]
    release = (release_artist or "").strip()
    if release and artist_name.startswith(release + ","):
        return release
    return split_featured(artist_name)[0]


_EDITION_MARKER = re.compile(rf"\b(?:{_EDITION_WORDS})\b", re.IGNORECASE)


def has_edition_marker(text: str | None) -> bool:
    """True when a release title or disambiguation names an edition ("20th anniversary
    deluxe edition", "Remastered"). Used to keep such releases out of the standard tracklist."""
    return bool(text and _EDITION_MARKER.search(text))


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


# Show lineups (Phase 3). Listing sites write one free-text title per show; these turn it
# into performer names. "&" and "and" never split here: "Simon & Garfunkel" is one act,
# and the artist lookup decides whether "A & B" is two (see musicdata.shows).

_QUOTED = re.compile(r"\s*[\"“”„‟][^\"“”„‟]*[\"“”„‟]")
_SUPPORT = re.compile(r"\s+(?:w/|with)\s+", re.IGNORECASE)
_TOUR = re.compile(r"\btour\b", re.IGNORECASE)
_PRESENTS = re.compile(r"^.*?\bpresents:?\s+", re.IGNORECASE)
_SEPARATORS = re.compile(r"\s+/\s+|\s*,\s+|\s+\+\s+")
_TRAILING_NOTE = re.compile(r"\s*\(([^()]*)\)\s*$")
_PARENTHESES = re.compile(r"\s*\([^()]*\)")
_NON_ARTIST = re.compile(
    r"\btribute\b|\bin concert\b|\bmusic of\b|\byears of\b|\bcelebrat\w*|\bsalute to\b"
    r"|\bthe making of\b|\blive to film\b|\bfilm with live\b|\bscreening\b",
    re.IGNORECASE,
)


def clean_performer(name: str) -> tuple[str, str | None]:
    """A performer as listed, without a trailing performance note: ("DIIV (DJ set)") →
    ("DIIV", "DJ set"). The note is kept for display; the name is what gets resolved."""
    s = " ".join(str(name or "").split())
    m = _TRAILING_NOTE.search(s)
    if m and m.start() > 0:
        return s[: m.start()].strip(), m.group(1).strip() or None
    return s, None


def non_artist_event(title: str) -> bool:
    """Tributes, film-in-concert screenings and the like: no performer is the artist named.
    Two or more quoted titles mark an album night ('Alice In Chains "Dirt", Pearl Jam "VS"');
    one quoted phrase is usually a tour name ('Don Omar "The Last King World Tour"')."""
    t = title or ""
    return bool(_NON_ARTIST.search(t)) or len(_QUOTED.findall(t)) >= 2


def _head(title: str) -> str:
    """Cut a promoter prefix and a tour or event name off the headliner part of a title."""
    title = _PRESENTS.sub("", title)
    if " - " in title:
        title = title.split(" - ", 1)[0]
    if ": " in title:
        left, right = title.split(": ", 1)
        title = right if _TOUR.search(left) and not _TOUR.search(right) else left
    return title


def split_lineup(title: str) -> list[str]:
    """Performer names from a show title, headliner first.

    "SYML - Solo in North America 2026 with Roger Weeks" → ["SYML", "Roger Weeks"];
    "Quintron and Miss Pussycat (with Michael Zerang) / Aaron Dilloway" →
    ["Quintron and Miss Pussycat", "Aaron Dilloway"]. Parts naming a tour are dropped.
    """
    s = " ".join(str(title or "").split())
    s = _QUOTED.sub("", s).strip() or s
    s = _PARENTHESES.sub("", s).strip() or s  # notes like "(with X on drums)" are not acts
    names: list[str] = []
    m = _SUPPORT.search(s)
    head, support = (s[: m.start()], s[m.end() :]) if m else (s, "")
    for chunk in (_head(head), support):
        for part in _SEPARATORS.split(chunk):
            name, _note = clean_performer(part)
            name = name.strip(" -:")
            if name and not _TOUR.search(name) and name not in names:
                names.append(name)
    return names
