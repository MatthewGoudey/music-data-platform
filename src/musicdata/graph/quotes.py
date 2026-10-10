"""`musicdata graph batch check-quotes <folder> <claims.jsonl>`: an extractor's self-check before
`load-claims`, the same Evidence and Structure rules `graph verify` applies (GRAPH_SPEC 8), read
against a batch folder's pages. No database, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

from musicdata.graph.verify import EVIDENCE_MAX, LINEAGE, PRED, norm


def batch_pages(folder: Path) -> dict[str, str]:
    """url -> normalised text for every `pages/*.md` in a batch folder (or a top-up folder)."""
    pages = {}
    for f in folder.glob("pages/*.md"):
        text = f.read_text("utf-8")
        url = next((ln[5:].strip() for ln in text.splitlines()[:8] if ln.startswith("url: ")), None)
        if url:
            pages[url] = norm(text)
    return pages


def claim_problems(c: dict, pages: dict[str, str]) -> list[str]:
    url, ev = c.get("source_url") or "", c.get("evidence") or ""
    problems = []
    if str(c.get("source", "")).startswith("map:"):
        problems.append("the atlas is not a source of claims")
    if c.get("predicate") not in PRED:
        problems.append("unknown predicate")
    if len(ev) > EVIDENCE_MAX:
        problems.append(f"evidence {len(ev)} characters > {EVIDENCE_MAX}")
    if c.get("predicate") in LINEAGE and c.get("direction") != "subject_newer":
        problems.append("lineage without direction subject_newer")
    hay = pages.get(url.split("#")[0]) if ev.startswith("field:") else pages.get(url)
    if hay is None:
        problems.append(f"source_url is not a page of this folder: {url}")
        return problems
    segs = (
        [norm(ev.split("=", 1)[-1])]
        if ev.startswith("field:")
        else [s.strip() for s in norm(ev).split("…") if s.strip()]
    )
    missing = [s for s in segs if s not in hay]
    if not segs or missing:
        problems.append("not on the page: " + " | ".join(m[:80] for m in missing))
    return problems


def check_file(folder: Path, claims_path: Path) -> list[str]:
    """One line per claim with a problem; empty when the file is ready to load."""
    pages = batch_pages(folder)
    out = []
    for n, line in enumerate(claims_path.read_text("utf-8").splitlines(), 1):
        if line.strip():
            c = json.loads(line)
            problems = claim_problems(c, pages)
            if problems:
                out.append(f"line {n} {c.get('claim_id')}: " + "; ".join(problems))
    return out
