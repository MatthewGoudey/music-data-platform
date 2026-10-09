"""Summarise a verified run: counts per album and predicate, the reading questions for Matt, rejections, credits.
Usage: python3 report.py <run_dir> <data_dir>   ->  <run_dir>/report.md"""

import json
import os
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
run, data = sys.argv[1], sys.argv[2]
rid = os.path.basename(run.rstrip("/"))
cs = [json.loads(ln) for ln in open(f"{run}/claims_verified.jsonl", encoding="utf-8")]
st = Counter(c["suggested_status"] for c in cs)
lines = [
    f"# Graph research run {rid}",
    "",
    f"Claims: {len(cs)} — " + ", ".join(f"{k} {v}" for k, v in st.most_common()),
    "",
]
lines += [
    "| Album | Claims | Accepted | Reading questions | Rejected |",
    "| --- | --- | --- | --- | --- |",
]
for a in sorted({c.get("album") for c in cs}):
    sub = [c for c in cs if c.get("album") == a]
    n = Counter(c["suggested_status"] for c in sub)
    lines.append(f"| {a} | {len(sub)} | {n['accepted']} | {n['ask_matt']} | {n['rejected']} |")
lines += ["", "| Predicate | Accepted | Mean confidence |", "| --- | --- | --- |"]
for p in sorted({c["predicate"] for c in cs}):
    acc = [c for c in cs if c["predicate"] == p and c["suggested_status"] == "accepted"]
    lines.append(
        f"| {p} | {len(acc)} | {sum(c['confidence'] for c in acc) / len(acc):.2f} |"
        if acc
        else f"| {p} | 0 | – |"
    )
q = [c for c in cs if c["suggested_status"] == "ask_matt"]
lines += [
    "",
    "## Reading questions for Matt",
    "Answer from the quote alone; no knowledge of the music is needed.",
    "",
]
for i, c in enumerate(q, 1):
    lines.append(
        f"{i}. **{c['subject']['name']} — {c['predicate']} — {c['object']['name']}?** Quote: “{c['evidence']}” ({c['source_url']}). The reader said: {c['checks'].get('reader', '').rstrip('.')}. Answer: yes / no / skip"
    )
lines += ["", "## Rejected", ""] + [
    f"- {c['claim_id']} {c['predicate']} {c['subject']['name']} → {c['object']['name']}: {'; '.join(c['fails'])}"
    for c in cs
    if c["suggested_status"] == "rejected"
]
sk = f"{run}/claims/{rid}_skipped.jsonl"
if os.path.exists(sk):
    lines += ["", "## Read but not claimed", ""] + [
        f"- {r['album']}: “{r['text']}” — {r['reason']}"
        for r in map(json.loads, open(sk, encoding="utf-8"))
    ]
led = f"{data}/credits.jsonl"
spent = (
    sum(
        json.loads(ln)["credits"]
        for ln in open(led, encoding="utf-8")
        if json.loads(ln)["run"] == rid
    )
    if os.path.exists(led)
    else 0
)
gaps = (
    [ln.strip() for ln in open(f"{data}/gaps.md", encoding="utf-8")]
    if os.path.exists(f"{data}/gaps.md")
    else []
)
lines += ["", f"Firecrawl credits this run: {spent}", "", "## Gaps", ""] + (
    [g for g in gaps if f"[{rid}]" in g] or ["none"]
)
open(f"{run}/report.md", "w", encoding="utf-8").write("\n".join(lines) + "\n")
print(f"{run}/report.md")
