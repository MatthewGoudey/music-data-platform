"""Seed readers on a small shared-shape list and on the real atlas (committed in seeds/)."""

from __future__ import annotations

from pathlib import Path

from musicdata.lists.seed import ListSpec, read_lanes, read_list, read_paths, read_registry

ROOT = Path(__file__).parents[2]


def _spec(slug: str, file: str, ranked: bool = True) -> ListSpec:
    return ListSpec(slug, slug, "canon", 1.0, ranked, "Recommended", file, None)


def test_a_shared_shape_list_keys_and_dedupes(tmp_path: Path) -> None:
    f = tmp_path / "seeds" / "lists" / "x.csv"
    f.parent.mkdir(parents=True)
    f.write_text(
        "position,artist,album,year,priority,genre,descriptors,note\n"
        "1,The Beach Boys,Pet Sounds,1966,,Baroque Pop,,\n"
        "2,Beach Boys,Pet Sounds (Remastered),1966,Essential,,,dup by key\n"
        "3,Wilco,Yankee Hotel Foxtrot,2002,Essential,Alt-Country,warm,\n",
        encoding="utf-8",
    )
    lf = read_list(_spec("x", "seeds/lists/x.csv"), tmp_path)
    assert (lf.rows, len(lf.entries), lf.duplicates, lf.keyless) == (3, 2, 1, 0)
    pet, yhf = lf.entries
    assert (pet.artist_key, pet.album_key, pet.position) == ("beach boys", "pet sounds", 1)
    assert pet.priority is None and pet.facets == {"genre": "Baroque Pop"}
    assert yhf.priority == "Essential" and yhf.year == 2002


def test_same_keys_years_apart_are_two_albums(tmp_path: Path) -> None:
    f = tmp_path / "seeds" / "lists" / "y.csv"
    f.parent.mkdir(parents=True)
    f.write_text(
        "position,artist,album,year,priority,genre,descriptors,note\n"
        "1,Weezer,Weezer,1994,,,,Blue\n"
        "2,Weezer,Weezer,2001,,,,Green\n"
        "3,Weezer,Weezer,2000,,,,a year from row 2: the same album\n"
        "4,D'Angelo,Black Messiah,2014,,,,\n"
        "5,D'Angelo,Black Messiah,2015,,,,one year apart: the same album\n",
        encoding="utf-8",
    )
    lf = read_list(_spec("y", "seeds/lists/y.csv"), tmp_path)
    assert [e.album_key for e in lf.entries] == ["weezer", "weezer #2001", "black messiah"]
    assert lf.duplicates == 2


def test_the_atlas_maps_lane_zone_layer_and_facets() -> None:
    spec = _spec("v_atlas", "seeds/atlas/albums.csv", ranked=False)
    lf = read_list(spec, ROOT)
    assert len(lf.entries) == 2942 and lf.duplicates == 0
    first = lf.entries[0]
    assert first.lane_id == "C1" and first.zone == "Core" and first.layer == "L1"
    assert first.facets["atlas_id"] == "A0001"
    assert "primary_lane" in first.facets and "description" in first.facets
    assert first.position is None
    assert sum(e.start_here for e in lf.entries) == 94


def test_lanes_and_paths_load_with_valid_parents() -> None:
    lanes = read_lanes(ROOT / "seeds" / "atlas" / "lanes.csv")
    ids = {x.lane_id for x in lanes}
    assert len(lanes) == 54 and all(p in ids for x in lanes for p in x.parents)
    assert len(read_paths(ROOT / "seeds" / "atlas" / "paths.csv")) == 111


def test_the_registry_reads(tmp_path: Path) -> None:
    f = tmp_path / "_lists.csv"
    f.write_text(
        "slug,name,goal,weight,ranked,default_priority,file,source\n"
        'rs,"Rolling Stone, 500",canon,1.0,true,Recommended,seeds/lists/rs.csv,src\n',
        encoding="utf-8",
    )
    (spec,) = read_registry(f)
    assert spec.ranked is True and spec.name == "Rolling Stone, 500" and spec.weight == 1.0
