"""What every page shares (docs/graph/COMPANION_SPEC.md section 6): the shell with the queue page's
look, links that carry the page token, and source tags."""

from __future__ import annotations

import html
from urllib.parse import quote, urlencode, urlparse

SITES = {
    "musicbrainz": "MB", "discogs": "Discogs", "wikipedia": "Wikipedia", "wikidata": "Wikidata",
    "matt": "Matt", "firecrawl_json": "Wikipedia",
}  # fmt: skip
HOSTS = {
    "en.wikipedia.org": "Wikipedia", "musicbrainz.org": "MB", "discogs.com": "Discogs",
    "noexpectations.fyi": "No Expectations", "pitchfork.com": "Pitchfork",
    "stereogum.com": "Stereogum", "allmusic.com": "AllMusic", "bandcamp.com": "Bandcamp",
    "uncut.co.uk": "Uncut", "uproxx.com": "Uproxx", "pastemagazine.com": "Paste",
    "rollingstone.com": "Rolling Stone", "theguardian.com": "The Guardian", "nme.com": "NME",
}  # fmt: skip


def esc(x: object) -> str:
    return "" if x is None else html.escape(str(x))


def link(path: str, t: str, **params: object) -> str:
    """A page URL carrying the page token."""
    q = urlencode({"t": t, **{k: v for k, v in params.items() if v is not None}})
    return f"{path}?{q}"


def site(url: str | None) -> str:
    host = (urlparse(url or "").hostname or "").removeprefix("www.")
    for k, v in HOSTS.items():
        if host == k or host.endswith("." + k):
            return v
    return host or "source"


def source_label(sources: list[str] | None, url: str | None) -> str:
    """The tag's text: the page's site when there is a page, else the first database."""
    if url:
        return site(url)
    for s in sources or []:
        if s in SITES:
            return SITES[s]
        if s.startswith("web:"):
            return site("https://" + s[4:])
    return "graph"


def src_tag(sources: list[str] | None, url: str | None, quote_text: str | None = None) -> str:
    label = esc(source_label(sources, url))
    title = f' title="{esc(quote_text[:200])}"' if quote_text else ""
    if url:
        return f'<a class="src" href="{esc(url)}" target="_blank" rel="noopener"{title}>{label}</a>'
    return f'<span class="src"{title}>{label}</span>'


def play_url(artist: str, album: str) -> str:
    return "https://open.spotify.com/search/" + quote(f"{artist} {album}")


CSS = """
:root {
  --bg: #eef0ec; --surface: #fbfcfa; --ink: #1b221f; --muted: #5d6a64; --line: #d5dbd6;
  --accent: #2c55c9; --accent-ink: #ffffff; --ok: #1f6b4f; --ok-bg: #dcefe5; --hi: #fff3c4;
  --display: "Bricolage Grotesque", "Segoe UI", system-ui, sans-serif;
  --body: "Atkinson Hyperlegible", "Segoe UI", system-ui, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, Consolas, monospace;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) { :root {
  --bg: #141917; --surface: #1c2320; --ink: #e6ebe8; --muted: #9aa8a1; --line: #2e3833;
  --accent: #7f9cf5; --accent-ink: #0f1420; --ok: #7fd1ad; --ok-bg: #1d3a2e; --hi: #3b3417;
  color-scheme: dark } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font-family: var(--body); font-size: 15px; line-height: 1.5; }
.wrap { max-width: 36rem; margin: 0 auto; padding-inline: 16px; padding-block: max(14px, env(safe-area-inset-top)) 80px; display: grid; gap: 18px; }
nav.top { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; font-size: 13px; }
nav.top a { color: var(--accent); font-weight: 700; text-decoration: none; }
nav.top form { flex: 1; display: flex; gap: 6px; min-width: 12rem; }
nav.top input { flex: 1; min-width: 0; font: 15px var(--body); padding: 6px 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); color: var(--ink); }
h1 { font-family: var(--display); font-weight: 700; font-size: 28px; line-height: 1.12; letter-spacing: -0.02em; margin: 0; overflow-wrap: anywhere; }
.by { font: 500 17px var(--display); color: var(--muted); }
.by a { color: inherit; }
.meta { font-size: 13px; color: var(--muted); }
h2 { font: 700 12.5px var(--mono); letter-spacing: .08em; text-transform: uppercase; color: var(--muted); margin: 6px 0 4px; }
section { display: grid; gap: 6px; min-width: 0; }
.box { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; display: grid; gap: 8px; min-width: 0; }
.row { display: grid; grid-template-columns: 7.5rem minmax(0, 1fr); gap: 8px; font-size: 14px; }
.row .k { color: var(--muted); font-size: 13px; }
ul.plain { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
ul.plain li { min-width: 0; overflow-wrap: anywhere; }
a { color: var(--accent); }
.tabs { display: flex; gap: 6px; flex-wrap: wrap; }
.tab { font: 600 13px var(--body); border: 1px solid var(--line); border-radius: 999px; padding: 5px 12px; text-decoration: none; color: var(--ink); background: var(--surface); }
.tab[aria-current="page"] { background: var(--ink); color: var(--bg); border-color: var(--ink); }
.tab.off { color: var(--muted); border-style: dashed; }
.tab.ask { cursor: pointer; color: var(--accent); border-color: var(--accent); background: transparent; }
.actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.btn { font: 600 13px var(--body); border: 1px solid var(--line); background: transparent; color: var(--ink); border-radius: 7px; padding: 7px 11px; text-decoration: none; cursor: pointer; }
.btn.play { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
.status { font: 600 11px var(--mono); letter-spacing: .05em; text-transform: uppercase; border-radius: 4px; padding: 2px 6px; background: var(--line); color: var(--ink); }
.status.heard { background: var(--ok-bg); color: var(--ok); }
.heard-mark { color: var(--ok); font-weight: 700; }
ol.tracks { list-style: none; margin: 0; padding: 0; display: grid; gap: 2px; }
ol.tracks li { display: grid; grid-template-columns: 2rem minmax(0, 1fr) auto; gap: 2px 10px; padding: 7px 8px; border-radius: 8px; scroll-margin-top: 80px; }
ol.tracks li.now { background: var(--hi); }
ol.tracks .n { font: 600 13px var(--mono); color: var(--muted); text-align: right; font-variant-numeric: tabular-nums; }
ol.tracks .len { font: 12px var(--mono); color: var(--muted); font-variant-numeric: tabular-nums; }
ol.tracks .notes { grid-column: 2 / -1; font-size: 13px; color: var(--muted); }
.quote { font-size: 13.5px; color: var(--muted); border-left: 3px solid var(--line); padding-left: 10px; margin-top: 2px; overflow-wrap: anywhere; }
.src { display: inline-block; font: 500 10.5px/1 var(--mono); color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, transparent); border-radius: 3px; padding: 3px 5px 2px; margin-left: 4px; text-decoration: none; vertical-align: 1px; }
.lack { font-size: 13.5px; color: var(--muted); }
.nowbar { position: sticky; top: 0; z-index: 2; background: var(--hi); color: var(--ink); border-radius: 8px; padding: 6px 10px; font-size: 13px; }
a:focus-visible, .btn:focus-visible, input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
"""


# Autocomplete for every search box (input[name=q] in a /pages/search form): served at
# /pages/suggest.js, it asks /pages/suggest as Matt types and lists up to 8 names under the box.
SUGGEST_JS = """
(() => {
  const css = document.createElement("style");
  css.textContent = `
.sg-wrap { position: relative; }
.sg { position: absolute; left: 0; right: 0; top: calc(100% + 4px); z-index: 50; margin: 0; padding: 4px;
  list-style: none; background: var(--surface, #fff); border: 1px solid var(--line, #ccc); border-radius: 10px;
  box-shadow: 0 8px 24px rgba(0,0,0,.18); max-height: 60vh; overflow-y: auto; min-width: 240px; }
.sg[hidden] { display: none !important; }
.sg a { display: block; padding: 8px 10px; border-radius: 7px; text-decoration: none; color: var(--ink, #111); }
.sg a.on, .sg a:hover { background: var(--hi, rgba(0,0,0,.06)); }
.sg .l { font-weight: 700; display: block; overflow-wrap: anywhere; }
.sg .m { font-size: 12.5px; color: var(--muted, #666); }`;
  document.head.appendChild(css);
  const t = new URLSearchParams(location.search).get("t") || "";
  const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"})[c]);
  document.querySelectorAll('form[action="/pages/search"] input[name=q]').forEach(input => {
    const form = input.form;
    form.classList.add("sg-wrap");
    input.setAttribute("autocomplete", "off");
    const box = document.createElement("ul");
    box.className = "sg"; box.hidden = true; box.setAttribute("role", "listbox");
    form.appendChild(box);
    let timer = 0, seq = 0, active = -1;
    const items = () => [...box.querySelectorAll("a")];
    const mark = () => items().forEach((a, i) => a.classList.toggle("on", i === active));
    input.addEventListener("input", () => {
      clearTimeout(timer);
      const q = input.value.trim();
      if (q.length < 2) { box.hidden = true; return; }
      timer = setTimeout(async () => {
        const mine = ++seq;
        try {
          const r = await fetch(`/pages/suggest?q=${encodeURIComponent(q)}&t=${encodeURIComponent(t)}`);
          if (!r.ok || mine !== seq) return;
          const list = await r.json();
          active = -1;
          box.innerHTML = list.map(s => `<li><a href="${esc(s.href)}"><span class="l">${esc(s.label)}</span>`
            + `<span class="m">${esc(s.meta)}</span></a></li>`).join("");
          box.hidden = !list.length;
        } catch (e) {}
      }, 150);
    });
    input.addEventListener("keydown", e => {
      const a = items();
      if (box.hidden || !a.length) return;
      if (e.key === "ArrowDown") { active = (active + 1) % a.length; mark(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { active = (active - 1 + a.length) % a.length; mark(); e.preventDefault(); }
      else if (e.key === "Enter" && active >= 0) { location.href = a[active].href; e.preventDefault(); }
      else if (e.key === "Escape") { box.hidden = true; }
    });
    input.addEventListener("blur", () => setTimeout(() => { box.hidden = true; }, 200));
    input.addEventListener("focus", () => { if (box.innerHTML && input.value.trim().length > 1) box.hidden = false; });
  });
})();
"""


def shell(title: str, body: str, t: str, *, script: str = "") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{esc(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700&family=Atkinson+Hyperlegible:wght@400;700&family=JetBrains+Mono:wght@400;600&display=swap">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<nav class="top" aria-label="Pages">
  <a href="{esc(link("/queue", t))}">← Up next</a>
  <form action="/pages/search" method="get" role="search">
    <input type="hidden" name="t" value="{esc(t)}">
    <input type="search" name="q" placeholder="Search albums, people, bands, songs" aria-label="Search">
  </form>
</nav>
{body}
</div>
{script}
<script src="{esc(link("/pages/suggest.js", t))}" defer></script>
</body>
</html>"""
