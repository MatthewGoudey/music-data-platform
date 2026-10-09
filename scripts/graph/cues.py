"""Print only the paragraphs of a cached page that can carry lineage, with paragraph numbers.
Usage: python3 cues.py <cache.md> [extra names...]"""

import re
import sys

sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
CUES = r"influen|inspir|indebted|reminisc|compar|evok|recall|echo|homage|tribute|in the vein|in the style|sound(s|ed)? like|cover(ed|s)?\b|sampl|interpolat|recorded with|backing|toured|member|formed|joined|lineage|predecessor|successor|followed|follow-up|debut|legacy|descendant|forebear|tradition|heir|kindred|recalls|nod to|borrow"
path, extra = sys.argv[1], sys.argv[2:]
pat = re.compile(CUES + ("|" + "|".join(map(re.escape, extra)) if extra else ""), re.I)
body = open(path, encoding="utf-8").read().split("\n---\n", 1)[-1]
paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
skip = re.compile(
    r"^(\||[-*] |\d+\. ↑|\[|!\[|#+ (References|External links|Notes|Sources|Track listing|Charts|Personnel))",
    re.I,
)
for i, p in enumerate(paras):
    if skip.match(p) or len(p) < 60:
        continue
    if pat.search(p):
        p = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", p)  # drop link targets to save tokens
        p = re.sub(r"\[\\?\[\d+\\?\]\]\([^)]+\)|\[\d+\]", "", p)  # citation markers
        p = re.sub(r' "[^"]+"\)', "", p)  # Wikipedia link-title remnants
        print(f"[¶{i}] {p}\n")
