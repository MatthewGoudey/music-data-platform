"""Fetch one page through Firecrawl into the shared cache, once per (url, schema version).
Usage: python3 fetch.py <entity_id> <url> <mode> <data_dir> <run_id> [--run-cap 500]
Modes: facts (Wikipedia: markdown + facts JSON, 5 credits), plain (1 credit), bandcamp (about/credits blocks, 1 credit).
Prints the cache file. Never retries: a failure goes to <data_dir>/gaps.md. Reads FIRECRAWL_API_KEY from the environment."""

import datetime
import json
import os
import re
import shutil
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252

T = os.path.dirname(os.path.abspath(__file__))
ent, url, mode, data, run = sys.argv[1:6]
cap = int(sys.argv[sys.argv.index("--run-cap") + 1]) if "--run-cap" in sys.argv else 500
COST = {"facts": 5, "plain": 1, "bandcamp": 1}
SCHEMA = {"facts": "facts-v2", "plain": "plain", "bandcamp": "bandcamp-v1"}[mode]
cache, ledger = f"{data}/cache", f"{data}/credits.jsonl"
os.makedirs(cache, exist_ok=True)
idx_p = f"{cache}/index.jsonl"
for line in open(idx_p, encoding="utf-8") if os.path.exists(idx_p) else []:
    r = json.loads(line)
    if r["url"] == url and r["schema_version"] == SCHEMA and not r.get("note"):
        print(f"{cache}/{r['file']}  (cached, 0 credits)")
        sys.exit(0)
spent = (
    sum(
        json.loads(ln)["credits"]
        for ln in open(ledger, encoding="utf-8")
        if json.loads(ln)["run"] == run
    )
    if os.path.exists(ledger)
    else 0
)
if spent + COST[mode] > cap:
    print(f"run cap reached: {spent} of {cap} credits spent in run {run}")
    sys.exit(3)
if not os.environ.get("FIRECRAWL_API_KEY") and os.path.exists(".env"):  # the repo's gitignored .env
    for line in open(".env", encoding="utf-8"):
        if line.strip().startswith("FIRECRAWL_API_KEY="):
            os.environ["FIRECRAWL_API_KEY"] = line.split("=", 1)[1].strip().strip('"')
if not os.environ.get("FIRECRAWL_API_KEY"):
    print("FIRECRAWL_API_KEY is not set (environment or .env)")
    sys.exit(2)
raw = f"{cache}/_raw_{os.getpid()}" + (".json" if mode == "facts" else ".md")

exe = shutil.which("firecrawl") or shutil.which("firecrawl.cmd")
if not exe:
    print("firecrawl CLI not found: npm install -g firecrawl-cli@latest")
    sys.exit(2)
cmd = [exe, "scrape", url, "-o", raw]
if mode == "facts":
    cmd += [
        "--format",
        "markdown,json",
        "--schema-file",
        f"{T}/facts_v2.schema.json",
        "--only-main-content",
    ]
elif mode == "bandcamp":
    cmd += [
        "--include-tags",
        "#name-section,.tralbum-about,.tralbum-credits,.tralbum-tags,#band-name-location",
    ]
else:
    cmd += ["--only-main-content"]
res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
ok = res.returncode == 0 and os.path.exists(raw) and os.path.getsize(raw) > 200
with open(ledger, "a", encoding="utf-8") as f:  # a failed page still costs a credit
    f.write(
        json.dumps(
            {
                "date": datetime.date.today().isoformat(),
                "run": run,
                "entity": ent,
                "url": url,
                "mode": mode,
                "credits": COST[mode] if ok else 1,
                "ok": ok,
            }
        )
        + "\n"
    )
if not ok:
    err = re.sub(
        r"fc-[A-Za-z0-9]+",
        "fc-***",
        (res.stderr or res.stdout or "empty page").strip().splitlines()[-1]
        if (res.stderr or res.stdout)
        else "empty page",
    )[:160]
    with open(f"{data}/gaps.md", "a", encoding="utf-8") as f:
        f.write(f"- [{run}] {ent} {url} | {err} | not retried\n")
    if os.path.exists(raw):
        os.remove(raw)
    print("gap logged:", err)
    sys.exit(1)
out = subprocess.run(
    [sys.executable, f"{T}/cache_split.py", raw, url, ent, SCHEMA, str(COST[mode]), cache],
    capture_output=True,
    text=True,
    encoding="utf-8",
    check=True,
).stdout.strip()
print(f"{cache}/{out}.md  ({COST[mode]} credits)")
