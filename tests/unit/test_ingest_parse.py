from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from musicdata.ingest.parse import parse_listen

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "listens.json").read_text("utf-8"))


def test_mapped_listen_carries_mbids() -> None:
    p = parse_listen(FIXTURES["mapped"])
    assert p is not None
    assert p.listened_at == datetime.fromtimestamp(1791409637, tz=UTC)
    assert p.artist.name == "Sigur Rós"
    assert p.artist.mbid == "f6f2326f-6b25-4170-b89d-e235b25508e8"
    assert p.artist.key == "sigur ros"
    assert p.release_group_mbid == "62b427b8-1c52-34e7-b250-da2aa3e44860"
    assert p.recording_mbid == "9c691c15-dba1-42c8-a99b-0aead1ec0bd4"
    assert p.norm_title == "staralfur"
    assert p.release_key == "agætis byrjun"  # æ is a letter, not a diacritic
    assert p.duration_ms == 406573
    assert p.client == "spotify.com"


def test_mapped_listen_belongs_to_its_first_credit() -> None:
    p = parse_listen(FIXTURES["mapped_featured"])
    assert p is not None
    assert p.artist.name == "Kendrick Lamar"
    assert p.artist.mbid == "381086ea-f511-4aba-bdf9-71c753dc5077"
    assert p.artist_name == "Kendrick Lamar, SZA"


def test_unmapped_listen_uses_keys_and_cuts_featured() -> None:
    p = parse_listen(FIXTURES["unmapped_featured"])
    assert p is not None
    assert p.artist.mbid is None
    assert p.artist.name == "Ryan Davis & the Roadhouse Band"
    assert p.artist.key == "ryan davis and the roadhouse band"
    assert p.release_group_mbid is None
    assert p.release_key == "new threats from the soul"
    assert p.norm_title == "mister bluebird"
    assert p.duration_ms == 201500
    assert p.client == "foobar2000"


def test_listen_without_release_has_no_release_key() -> None:
    p = parse_listen(FIXTURES["no_release"])
    assert p is not None
    assert p.release_name is None
    assert p.release_key == ""


def test_listen_without_artist_is_skipped() -> None:
    assert parse_listen(FIXTURES["no_artist"]) is None


def test_comma_joined_artists_belong_to_the_album_artist() -> None:
    p = parse_listen(FIXTURES["unmapped_comma"])
    assert p is not None
    assert p.artist.name == "Kendrick Lamar" and p.artist.key == "kendrick lamar"
    assert p.artist_name == "Kendrick Lamar, U2"
    assert p.norm_title == "xxx"
