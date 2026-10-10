"""Document pages (companion spec 7.5): markers become source tags, raw HTML is escaped, track
anchors survive, and the Sources section is the page's own."""

from __future__ import annotations

from musicdata.documents import render

DOC = """# Title line

## Before you press play

It was recorded at Drop of Sun [c:12][p:2]. A <script>alert(1)</script> stays text [p:2].

## Track by track

### 1. Hangover Game
<a id="track-1"></a>

Written by MJ Lenderman [c:12].

## Sources

[c:12] https://example.com/claim
"""
CITES = {
    "c:12": {"label": "MusicBrainz", "url": "https://musicbrainz.org/x", "title": "credit"},
    "p:2": {"label": "Wikipedia", "url": "https://en.wikipedia.org/wiki/Boat_Songs",
            "title": "Boat Songs - Wikipedia"},
}  # fmt: skip


def page() -> str:
    return render(DOC, CITES, title="Boat Songs", artist="MJ Lenderman", facts=["2022"],
                  kind="deep_dive", checks={"sentences": 3, "supported": 3})  # fmt: skip


def test_markers_become_source_tags() -> None:
    html = page()
    assert "[c:12]" not in html and "[p:2]" not in html
    assert html.count('class="src"') == 4  # MusicBrainz + Wikipedia, Wikipedia, MusicBrainz
    assert 'href="https://en.wikipedia.org/wiki/Boat_Songs"' in html


def test_raw_html_is_escaped_and_anchors_survive() -> None:
    html = page()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert '<h3 id="track-1"><span class="no">1</span>Hangover Game</h3>' in html
    assert 'href="#track-1"' in html


def test_the_page_sets_its_own_header_and_sources() -> None:
    html = page()
    assert "<title>Boat Songs · Deep dive</title>" in html
    assert "Title line" not in html  # the Markdown's own H1 gives way to the masthead
    assert "https://example.com/claim" not in html  # the Markdown's Sources list is replaced
    assert "Boat Songs - Wikipedia" in html and "3 cited sentences checked" in html
