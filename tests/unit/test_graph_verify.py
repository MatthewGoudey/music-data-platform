"""Verification (GRAPH_SPEC 8): the T1 golden run, the negative cases and the normaliser."""

from __future__ import annotations

import copy
import csv
import json
from pathlib import Path

import pytest

from musicdata.graph.baseline import musical_roles
from musicdata.graph.facts import facts_claims
from musicdata.graph.seed import read_predicates
from musicdata.graph.verify import (
    PRED,
    Lookups,
    match_key,
    name_on_page,
    norm,
    same_name,
    verify,
)

T1 = Path(__file__).parent / "fixtures" / "graph" / "T1"
GOLDEN = Path(__file__).parent / "golden" / "graph_normalize.json"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text("utf-8").splitlines() if ln.strip()]


def t1_claims() -> list[dict]:
    """The T1 claim files as the prototype wrote them; database and Firecrawl claims carry
    their extractor in `source` there."""
    claims = [
        c
        for f in sorted((T1 / "claims").glob("*.jsonl"))
        if "skipped" not in f.name
        for c in _jsonl(f)
    ]
    for c in claims:
        c.setdefault("extractor", c["source"])
    return claims


def t1_lookups(reader: str = "reader_output.jsonl") -> Lookups:
    index = _jsonl(T1 / "cache" / "index.jsonl")
    raw = json.loads((T1 / "lookups.json").read_text("utf-8"))
    with (T1 / "album_notes.csv").open(encoding="utf-8") as f:
        notes = {r["atlas_id"]: r for r in csv.DictReader(f)}
    return Lookups(
        pages={r["url"]: norm((T1 / "cache" / r["file"]).read_text("utf-8")) for r in index},
        notes=notes,
        works={match_key(w["title"].split("(")[0]): w for w in raw["works"]},
        artists={match_key(a["name"]): a for a in raw["artists"]},
        foreign={
            name: {
                "status": fb["status"],
                "musicbrainz": {match_key(x) for x in fb["musicbrainz"]},
                "discogs": {match_key(x) for x in fb["discogs"]},
            }
            for name, fb in raw["foreign"].items()
        },
        years={(t, n): y for t, n, y in raw["years"]},
        album_years=raw["album_years"],
        reader={r["claim_id"]: r for r in _jsonl(T1 / reader)},
    )


@pytest.fixture(scope="module")
def t1() -> tuple[list[dict], dict[str, dict]]:
    expected = {c["claim_id"]: c for c in _jsonl(T1 / "claims_verified.jsonl")}
    return verify(t1_claims(), t1_lookups()), expected


def test_t1_golden_statuses(t1) -> None:
    got, _ = t1
    assert len(got) == 126
    statuses = {c["claim_id"]: c["suggested_status"] for c in got}
    assert list(statuses.values()).count("accepted") == 125
    assert [k for k, v in statuses.items() if v != "accepted"] == ["T1-A2184-L027"]
    assert statuses["T1-A2184-L027"] == "ask_matt"


def _expected_confidence(e: dict) -> float:
    """The prototype's confidence, less 0.1 for an atlas comparison below the cap: spec v4
    starts `map:*` inferred claims at 0.5, a critic's level (the atlas is AI-written)."""
    if e["source"].startswith("map:") and e["basis"] == "inferred" and e["confidence"] < 0.95:
        return round(e["confidence"] - 0.1, 2)
    return e["confidence"]


def test_t1_golden_matches_the_prototype_claim_by_claim(t1) -> None:
    got, expected = t1
    for c in got:
        e = expected[c["claim_id"]]
        assert (c["suggested_status"], c["confidence"], c["fails"]) == (
            e["suggested_status"],
            _expected_confidence(e),
            e["fails"],
        ), c["claim_id"]
        assert c["independent_support"] == e["independent_support"], c["claim_id"]


def test_t1_first_pass_reader_flags_what_round_two_fixed() -> None:
    first = verify(t1_claims(), t1_lookups("reader_output_round1.jsonl"))
    flagged = {c["claim_id"] for c in first if c["suggested_status"] != "accepted"}
    assert flagged and "T1-A0015-F001" not in flagged  # databases never wait for the reader


def test_t1_ported_facts_match_the_firecrawl_claims() -> None:
    index = _jsonl(T1 / "cache" / "index.jsonl")
    ported = set()
    for r in index:
        facts = T1 / "cache" / r["file"].replace(".md", ".facts.json")
        if r.get("facts") and facts.exists():
            for c in facts_claims(json.loads(facts.read_text("utf-8")), r["url"]):
                ported.add((c.predicate, c.evidence))
    prototype = set()
    for c in t1_claims():
        if c["source"] != "firecrawl_json":
            continue
        if c["predicate"] == "credited_on":  # spec v5: artwork and layout are not credits
            roles = musical_roles(c["qualifiers"]["role"])
            if not roles:
                continue
            name = c["subject"]["name"]
            prototype.add(("credited_on", f"Wikipedia personnel: {name} — {', '.join(roles)}"))
        else:
            prototype.add((c["predicate"], c["evidence"]))
    assert ported == prototype


def test_pred_matches_the_predicate_seed() -> None:
    seed = {p["name"]: p for p in read_predicates()}
    assert set(seed) == set(PRED)
    for name, (st, ot) in PRED.items():
        assert set(seed[name]["subject_types"]) <= st, name
        assert set(seed[name]["object_types"]) <= ot, name


# --- negative cases (GRAPH_SPEC 12) ---------------------------------------------------------


def _one(claims: list[dict], label: str) -> dict:
    return next(c for c in claims if c["claim_id"] == label)


def _rerun(change, lookups: Lookups | None = None) -> list[dict]:
    claims = t1_claims()
    change(claims)
    return verify(claims, lookups or t1_lookups())


def _cover() -> str:
    return next(c["claim_id"] for c in t1_claims() if c["predicate"] == "covers"
                and "Gone Dead Train" in c["object"]["name"])  # fmt: skip


def test_cover_pointed_at_a_later_recording_is_rejected() -> None:
    label = _cover()

    def change(claims: list[dict]) -> None:
        c = _one(claims, label)
        c["subject"], c["object"] = copy.deepcopy(c["object"]), copy.deepcopy(c["subject"])
        c["object"]["year"] = None
        c["subject"]["year"] = None

    c = _one(_rerun(change), label)
    assert c["suggested_status"] == "rejected"
    assert any("first recording" in f for f in c["fails"]), c["fails"]


def test_lineage_with_subject_and_object_swapped_is_rejected_by_dates() -> None:
    label = "T1-A0015-L001"

    def change(claims: list[dict]) -> None:
        c = _one(claims, label)
        c["subject"], c["object"] = c["object"], c["subject"]
        c["predicate"] = "influenced_by"
        c["basis"] = "reported"
        c["subject"] = {"type": "artist", "name": "MJ Lenderman", "year": 2022}
        c["object"] = {"type": "artist", "name": "Neil Young & Crazy Horse"}
        c["object"]["year"] = 2023

    c = _one(_rerun(change), label)
    assert any(f.startswith("direction:") for f in c["fails"]), c["fails"]


def test_sounds_like_must_be_inferred() -> None:
    label = "T1-A0015-L002"
    c = _one(_rerun(lambda cs: _one(cs, label).update(basis="reported")), label)
    assert "sounds_like must be inferred" in c["fails"]


def test_quote_not_on_the_page_is_rejected() -> None:
    label = "T1-A0015-L002"
    c = _one(
        _rerun(lambda cs: _one(cs, label).update(evidence="Jason Molina's finest hour")), label
    )
    assert "evidence not found verbatim in the cached source" in c["fails"]


def test_firecrawl_name_missing_from_the_page_is_rejected() -> None:
    label = next(c["claim_id"] for c in t1_claims() if c["source"] == "firecrawl_json")

    def change(claims: list[dict]) -> None:
        c = _one(claims, label)
        who = "object" if c["predicate"] == "recorded_at" else "subject"
        c[who] = {**c[who], "name": "Nobody Imaginary"}

    c = _one(_rerun(change), label)
    assert "extracted name not on the cached page" in c["fails"]


def test_atlas_note_citing_the_same_page_counts_once() -> None:
    lk = t1_lookups()
    claims = t1_claims()
    atlas = next(c for c in claims if c["source"].startswith("map:"))
    twin = copy.deepcopy(atlas)
    twin.update(
        claim_id="T1-TWIN",
        source="wikipedia",
        source_url="https://en.wikipedia.org/wiki/Boat_Songs",
    )
    claims.append(twin)
    note = lk.notes[atlas["album"]]
    lk.notes[atlas["album"]] = {**note, "source_urls": "https://en.wikipedia.org/wiki/Boat_Songs"}
    c = _one(verify(claims, lk), atlas["claim_id"])
    assert "counted once" in c["checks"].get("independence", "")
    assert "wikipedia" not in c["independent_support"]


def test_reader_partial_without_a_database_asks_matt() -> None:
    lk = t1_lookups()
    lk.reader["T1-A0015-L002"] = {"verdict": "PARTIAL", "reason": "only a comparison"}
    assert _one(verify(t1_claims(), lk), "T1-A0015-L002")["suggested_status"] == "ask_matt"
    lk.reader["T1-A0015-L002"] = {"verdict": "DOES_NOT_SUPPORT", "reason": "no"}
    assert _one(verify(t1_claims(), lk), "T1-A0015-L002")["suggested_status"] == "rejected"
    del lk.reader["T1-A0015-L002"]
    assert _one(verify(t1_claims(), lk), "T1-A0015-L002")["suggested_status"] == "unread"


def test_same_name_rule() -> None:
    assert same_name("Wally Heider", "Wally Heider Studios")
    assert same_name("Neil Young & Crazy Horse", "neil young and crazy horse")
    assert not same_name("Jack", "Jack Nitzsche")  # under 5 characters
    assert not same_name("", "")


def test_normaliser_golden_file() -> None:
    for case in json.loads(GOLDEN.read_text("utf-8")):
        assert norm(case["in"]) == case["out"], case["in"]


def test_name_on_page_ignores_quote_marks_and_a_studio_suffix() -> None:
    page = norm(
        '- ["Sneaky" Pete Kleinow](https://en.wikipedia.org/wiki/Sneaky_Pete_Kleinow) – pedal steel'
        " | Studio | - [Wally Heider](https://en.wikipedia.org/wiki/Wally_Heider_Studios)"
        " recorded at various places across Los Angeles"
    )
    assert name_on_page("Sneaky Pete Kleinow", page)
    assert name_on_page("Wally Heider Studios", page, place=True)
    assert name_on_page("Wally Heider Studio 3", page, place=True)
    assert not name_on_page("Wally Heider Studios", page)  # people keep the strict rule
    assert not name_on_page("Various studios", page, place=True)
    assert not name_on_page("Frank Sampedro", page)
