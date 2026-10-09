"""Split a Firecrawl markdown+json scrape into the cache: <slug>.md with a header, facts JSON, and an index line.
Usage: python3 cache_split.py <raw.json|raw.md> <url> <entity_id> <schema_version> <credits> <cache_dir>"""

import datetime
import hashlib
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
raw, url, ent, schema, credits, cache = sys.argv[1:7]
slug = hashlib.sha1(url.encode()).hexdigest()[:12]
today = datetime.date.today().isoformat()
if raw.endswith(".json"):
    d = json.load(open(raw, encoding="utf-8"))
    md = d.get("markdown", "")
    facts = d.get("json")
    title = d.get("metadata", {}).get("title", "")
else:
    md = open(raw, encoding="utf-8").read()
    facts = None
    title = ""
with open(os.path.join(cache, f"{slug}.md"), "w", encoding="utf-8") as f:
    f.write(
        f"---\nurl: {url}\ntitle: {title}\nfetched: {today}\nentity: {ent}\nschema_version: {schema}\n---\n\n{md}"
    )
if facts is not None:
    json.dump(
        facts,
        open(os.path.join(cache, f"{slug}.facts.json"), "w", encoding="utf-8"),
        indent=1,
        ensure_ascii=False,
    )
with open(os.path.join(cache, "index.jsonl"), "a", encoding="utf-8") as f:
    f.write(
        json.dumps(
            {
                "url": url,
                "schema_version": schema,
                "entity": ent,
                "fetched_at": today,
                "credits": int(credits),
                "file": f"{slug}.md",
                "facts": facts is not None,
            }
        )
        + "\n"
    )
os.remove(raw)
print(slug)
