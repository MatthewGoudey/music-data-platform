"""The paragraphs of a page that can carry lineage (port of `scripts/graph/cues.py`): what the
reading step reads first, with paragraph numbers, link targets and citations dropped."""

from __future__ import annotations

import re

CUES = (
    r"influen|inspir|indebted|reminisc|compar|evok|recall|echo|homage|tribute|in the vein"
    r"|in the style|sound(s|ed)? like|cover(ed|s)?\b|sampl|interpolat|recorded with|backing"
    r"|toured|member|formed|joined|lineage|predecessor|successor|followed|follow-up|debut"
    r"|legacy|descendant|forebear|tradition|heir|kindred|recalls|nod to|borrow"
)
_SKIP = re.compile(
    r"^(\||[-*] |\d+\. ↑|\[|!\[|#+ (References|External links|Notes|Sources|Track listing"
    r"|Charts|Personnel))",
    re.I,
)
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_CITE = re.compile(r"\[\\?\[\d+\\?\]\]\([^)]+\)|\[\d+\]")
_TITLE_REMNANT = re.compile(r' "[^"]+"\)')


def cue_paragraphs(body: str, extra: list[str] | tuple[str, ...] = ()) -> list[str]:
    """`[¶n] paragraph` for each paragraph of 60+ characters that matches a cue word or one
    of the extra names. A header block ending in a `---` line is skipped."""
    pat = re.compile(CUES + "".join("|" + re.escape(x) for x in extra if x), re.I)
    text = body.split("\n---\n", 1)[-1]
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out = []
    for i, p in enumerate(paras):
        if _SKIP.match(p) or len(p) < 60 or not pat.search(p):
            continue
        p = _TITLE_REMNANT.sub("", _CITE.sub("", _LINK.sub(r"\1", p)))
        out.append(f"[¶{i}] {p}")
    return out
