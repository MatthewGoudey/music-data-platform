"""A critic's sitemap and post matching (no network)."""

from __future__ import annotations

from musicdata.graph.critics import SITES, mentions, post_urls
from musicdata.graph.verify import norm

# What Firecrawl returns for the sitemap: URLs and dates run together.
SITEMAP = (
    "https://www.noexpectations.fyi/archivedaily"
    "https://www.noexpectations.fyi/p/greg-freeman-curbside-lambsear-andy-boay2026-10-08monthly"
    "https://www.noexpectations.fyi/p/the-100-best-albums-of-20252025-12-11monthly"
    "https://www.noexpectations.fyi/p/the-60-best-albums-of-2022-according2023-02-15monthly"
    "https://www.noexpectations.fyi/p/greg-freeman-curbside-lambsear-andy-boay2026-10-08monthly"
)


def test_post_urls_split_urls_from_dates() -> None:
    assert post_urls(SITEMAP, SITES["noexpectations"]["post"]) == [
        "https://www.noexpectations.fyi/p/greg-freeman-curbside-lambsear-andy-boay",
        "https://www.noexpectations.fyi/p/the-100-best-albums-of-2025",
        "https://www.noexpectations.fyi/p/the-60-best-albums-of-2022-according",
    ]


def test_a_post_matches_when_it_names_artist_and_title() -> None:
    body = norm("**Greg Freeman** – [Burnover](https://x.bandcamp.com) is a wonder.")
    assert mentions(body, "Greg Freeman", "Burnover")
    assert not mentions(body, "Greg Freeman", "I Looked Out")
    assert not mentions(norm("Hello III and Greg Freeman"), "Greg Freeman", "III")  # too short
