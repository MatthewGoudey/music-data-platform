"""The free baseline's claims from MusicBrainz and Discogs JSON (graph/baseline.py)."""

from __future__ import annotations

from musicdata.clients.discogs import clean_name, clean_role, release_id_from
from musicdata.graph.baseline import (
    ALBUM,
    artist_claims,
    discogs_claims,
    link_kind,
    musicbrainz_claims,
    url_links,
)


def _artist(name: str, mbid: str, type_: str = "Person") -> dict:
    return {"name": name, "id": mbid, "type": type_}


RELEASE = {
    "id": "rel-1",
    "relations": [
        {"target-type": "artist", "type": "producer", "attributes": ["co"],
         "artist": _artist("Jack Nitzsche", "a-jn")},
        {"target-type": "place", "type": "recorded at", "begin": "1969-01",
         "place": {"name": "Wally Heider Studios", "id": "p-wh"}},
    ],
    "label-info": [{"catalog-number": "RS 6349", "label": {"name": "Reprise", "id": "l-rep"}}],
    "media": [{"tracks": [
        {"title": "Cinnamon Girl", "recording": {"relations": [
            {"target-type": "artist", "type": "instrument", "attributes": ["piano", "guest"],
             "artist": _artist("Jack Nitzsche", "a-jn")},
            {"target-type": "work", "work": {"relations": [
                {"target-type": "artist", "type": "composer", "artist": _artist("Neil Young", "a-ny")},
            ]}},
        ]}},
        {"title": "Down by the River", "recording": {"relations": [
            {"target-type": "place", "type": "recorded at", "place": {"name": "Wally Heider Studios", "id": "p-wh"}},
            {"target-type": "work", "work": {"relations": [
                {"target-type": "artist", "type": "writer", "artist": _artist("Neil Young", "a-ny")},
            ]}},
        ]}},
    ]}],
}  # fmt: skip

BAND = {
    "id": "a-ch",
    "name": "Crazy Horse",
    "type": "Group",
    "area": {"name": "United States", "id": "ar-us"},
    "begin-area": {"name": "Los Angeles", "id": "ar-la"},
    "relations": [
        {"target-type": "artist", "type": "member of band", "direction": "backward",
         "begin": "1968", "end": None, "attributes": ["guitar"], "artist": _artist("Danny Whitten", "a-dw")},
        {"target-type": "artist", "type": "member of band", "direction": "backward",
         "artist": _artist("Danny Whitten", "a-dw")},
    ],
}  # fmt: skip


def test_credits_merge_per_person_with_roles_and_tracks() -> None:
    claims = musicbrainz_claims(RELEASE, [])
    credits = [c for c in claims if c.predicate == "credited_on"]
    nitzsche = next(c for c in credits if c.subject.name == "Jack Nitzsche")
    assert nitzsche.obj is ALBUM and nitzsche.subject.type == "person"
    assert nitzsche.qualifiers == {  # co-producer of the album, piano on one track
        "role": ["co-producer", "piano"],
        "tracks": ["Cinnamon Girl"],
        "album_wide": True,
    }
    assert nitzsche.evidence == "MusicBrainz release credits: Jack Nitzsche — co-producer, piano"
    assert nitzsche.source_url == "https://musicbrainz.org/release/rel-1"
    writer = next(c for c in credits if c.subject.name == "Neil Young")
    assert writer.qualifiers == {
        "role": ["songwriter"],
        "tracks": ["Cinnamon Girl", "Down by the River"],
    }


def test_places_once_per_role_and_labels() -> None:
    claims = musicbrainz_claims(RELEASE, [])
    places = [c for c in claims if c.predicate == "recorded_at"]
    assert len(places) == 1 and places[0].obj.mbid == "p-wh"
    assert places[0].qualifiers == {"mb_role": "recorded at", "dates": "1969-01"}
    (label,) = (c for c in claims if c.predicate == "released_by")
    assert label.evidence == "MusicBrainz label info: Reprise RS 6349"


def test_band_members_point_person_to_group_and_area_uses_begin_area() -> None:
    claims = artist_claims(BAND)
    members = [c for c in claims if c.predicate == "member_of"]
    assert len(members) == 1
    m = members[0]
    assert (m.subject.name, m.subject.type, m.obj.name, m.obj.type) == (
        "Danny Whitten", "person", "Crazy Horse", "artist",
    )  # fmt: skip
    assert m.qualifiers == {"from": "1968", "to": None, "instrument": ["guitar"]}
    (area,) = (c for c in claims if c.predicate == "based_in")
    assert area.obj.name == "Los Angeles"
    assert area.evidence == "MusicBrainz area: United States; begin area: Los Angeles"


def test_discogs_people_labels_and_styles() -> None:
    dg = {
        "url": "https://www.discogs.com/release/9",
        "credits": [
            {"name": "Ben Keith (2)", "role": "Guitar [Pedal Steel]"},
            {"name": "Ben Keith (2)", "role": "Dobro"},
        ],
        "labels": [{"name": "Reprise Records (2)", "catno": "MS 2032"}],
        "styles": ["Country Rock"],
    }
    claims = discogs_claims(dg)
    (keith,) = (c for c in claims if c.predicate == "credited_on")
    assert keith.subject.name == "Ben Keith" and keith.qualifiers == {"role": ["Dobro", "Guitar"]}
    (label,) = (c for c in claims if c.predicate == "released_by")
    assert label.obj.name == "Reprise Records"
    (style,) = (c for c in claims if c.predicate == "has_genre")
    assert style.qualifiers == {"vocabulary": "discogs_style"} and style.extractor == "discogs"


def test_links_and_discogs_ids() -> None:
    assert link_kind("https://en.wikipedia.org/wiki/Zuma") == "wikipedia"
    assert link_kind("https://neilyoung.bandcamp.com/album/x") == "bandcamp"
    assert link_kind("https://open.spotify.com/album/1") == "streaming"
    assert link_kind("https://neilyoung.com", "official homepage") == "official"
    rg = {
        "relations": [
            {"type": "discogs", "url": {"resource": "https://www.discogs.com/master/123"}}
        ]
    }
    assert url_links(rg) == [("discogs", "https://www.discogs.com/master/123")]
    assert release_id_from(
        ["https://www.discogs.com/release/9", "https://www.discogs.com/master/7"]
    ) == ("master", "7")
    assert (
        clean_name("Jim Keltner (2)") == "Jim Keltner" and clean_role("Guitar [Lead]") == "Guitar"
    )


def test_artwork_and_business_credits_are_not_work_on_the_music() -> None:
    from musicdata.graph.baseline import musical_roles

    assert musical_roles(["Photography By", "Artwork", "Design", "Management", "A&R"]) == []
    assert musical_roles(["Guitar", "Cover", "Mixed By", "Sound Design"]) == [
        "Guitar",
        "Mixed By",
        "Sound Design",
    ]
    assert musical_roles(["Counterpart", "Party Noise"]) == ["Counterpart", "Party Noise"]
