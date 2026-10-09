"""Claim identity (GRAPH_SPEC section 4): one `assertion` row per distinct claim.

`claim_key` is the sha1 of subject, predicate, object, source, source URL and evidence, so
loading the same claim twice is a no-op, while the same edge from another source or quote is
a second claim (that is how agreement is counted).
"""

from __future__ import annotations

import hashlib


def claim_key(
    subject_id: int,
    predicate: str,
    obj: int | str,
    source: str,
    source_url: str | None,
    evidence: str,
) -> str:
    parts = [str(subject_id), predicate, str(obj), source, source_url or "", evidence]
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()
