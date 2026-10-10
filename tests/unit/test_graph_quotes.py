"""The extractor's quote self-check (no database)."""

from __future__ import annotations

import json
from pathlib import Path

from musicdata.graph.quotes import check_file

PAGE = """---
url: https://example.com/review
title: A review
---

Critics compared [the record](https://x.example) to Zz Old Band.[3] RIYL: Zz Older Band.
"""


def _claim(**kw) -> dict:
    return {"claim_id": "P99-A0001-L001", "predicate": "sounds_like", "source": "web:example.com",
            "source_url": "https://example.com/review", "direction": "subject_newer",
            "evidence": "Critics compared the record to Zz Old Band.", **kw}  # fmt: skip


def test_check_quotes(tmp_path: Path) -> None:
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "1.md").write_text(PAGE, "utf-8")
    claims = [
        _claim(),
        _claim(claim_id="L002", evidence="Critics compared … RIYL: Zz Older Band"),
        _claim(claim_id="L003", evidence="a sentence nowhere on the page"),
        _claim(claim_id="L004", source="map:v_atlas"),
        _claim(claim_id="L005", direction=None),
        _claim(claim_id="L006", source_url="https://elsewhere.example"),
    ]
    f = tmp_path / "claims.jsonl"
    f.write_text("\n".join(json.dumps(c) for c in claims), "utf-8")
    problems = check_file(tmp_path, f)
    assert [p.split(":")[0] for p in problems] == ["line 3 L003", "line 4 L004", "line 5 L005",
                                                  "line 6 L006"]  # fmt: skip
    assert "not on the page" in problems[0] and "atlas" in problems[1]
