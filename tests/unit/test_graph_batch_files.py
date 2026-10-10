"""Reading batches: reader input, cue paragraphs and MusicBrainz answers (no network)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from musicdata.graph import names
from musicdata.graph.batch import needs_reader, reader_line
from musicdata.graph.cues import cue_paragraphs

T1 = Path(__file__).parent / "fixtures" / "graph" / "T1"


def test_reader_input_matches_the_prototype_on_t1() -> None:
    with (T1 / "albums.csv").open(encoding="utf-8") as f:
        albums = {r["atlas_id"]: f"{r['artist']} – {r['album']}" for r in csv.DictReader(f)}
    claims = [
        json.loads(ln) for ln in (T1 / "claims_verified.jsonl").read_text("utf-8").splitlines()
    ]
    for c in claims:
        c.setdefault("extractor", c["source"])
    lines = [reader_line(c, albums.get(c["album"], c["album"])) for c in claims if needs_reader(c)]
    assert lines == (T1 / "reader_input.txt").read_text("utf-8").splitlines()


def test_cue_paragraphs_keep_lineage_prose_only() -> None:
    body = (T1 / "cache" / "28930b8e187d.md").read_text("utf-8")
    cues = cue_paragraphs(body, ["Lenderman"])
    assert any("comparing the album to Jason Molina" in p for p in cues)
    assert all(p.startswith("[¶") for p in cues)
    assert not any("](http" in p or "cite_note" in p for p in cues)
    assert not any(p.split("] ", 1)[1].startswith("|") for p in cues)


def test_exact_artists_and_joint_credits() -> None:
    hits = [
        {"id": "1", "name": "Crazy Horse", "type": "Group", "life-span": {"begin": "1968"}},
        {"id": "2", "name": "Crazy Horse Band", "aliases": [{"name": "crazy horse"}]},
        {"id": "3", "name": "Crazy Horses"},
    ]
    assert [a["id"] for a in names.exact_artists(hits, "Crazy Horse")] == ["1", "2"]
    assert names.artist_attrs(hits[0])["year"] == 1968
    assert names.joint_parts("Neil Young & Crazy Horse") == ["Neil Young", "Crazy Horse"]
    assert names.joint_parts("Crazy Horse") == []


def test_work_sample_keeps_each_artists_first_recording() -> None:
    work = {
        "id": "w1",
        "title": "Gone Dead Train",
        "relations": [
            {"target-type": "artist", "artist": {"name": "Jack Nitzsche"}},
            {"target-type": "recording", "recording": {"id": "r2"}, "attributes": ["cover"]},
        ],
    }
    recs = [
        {"id": "r2", "title": "Gone Dead Train", "first-release-date": "1971-02",
         "artist-credit": [{"name": "Crazy Horse"}]},
        {"id": "r1", "title": "Gone Dead Train", "first-release-date": "1970-08",
         "artist-credit": [{"name": "Randy Newman"}]},
        {"id": "r3", "title": "Gone Dead Train (live)", "first-release-date": "1999",
         "artist-credit": [{"name": "Crazy Horse"}]},
    ]  # fmt: skip
    w = names.work_sample(work, recs)
    assert [(r["artist"], r["first_release"], r["cover"]) for r in w["sample"]] == [
        ("Randy Newman", "1970-08", False),
        ("Crazy Horse", "1971-02", True),
    ]
    assert w["writers"] == ["Jack Nitzsche"] and w["recordings"] == 2


def test_release_works_and_credit_names() -> None:
    release = {
        "artist-credit": [{"name": "Neil Young"}],
        "relations": [{"target-type": "artist", "artist": {"name": "David Briggs"}}],
        "media": [
            {
                "tracks": [
                    {
                        "title": "Cinnamon Girl",
                        "recording": {
                            "title": "Cinnamon Girl",
                            "relations": [
                                {"target-type": "artist", "artist": {"name": "Danny Whitten"}},
                                {"target-type": "work",
                                 "work": {"id": "w9", "title": "Cinnamon Girl"}},
                            ],
                        },
                    }
                ]
            }
        ],
    }  # fmt: skip
    works = names.release_works(release)
    assert works == [{"track": "Cinnamon Girl", "work": "Cinnamon Girl", "work_mbid": "w9"}]
    assert names.find_work(works, "Cinnamon Girl (album version)")["work_mbid"] == "w9"
    assert names.release_credit_names(release) == {"neil young", "david briggs", "danny whitten"}
