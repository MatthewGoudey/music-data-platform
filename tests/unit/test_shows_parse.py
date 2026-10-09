"""Parsers against real pages saved on 2026-10-08 (tests/unit/fixtures)."""

from __future__ import annotations

from pathlib import Path

from musicdata.shows.parse import (
    parse_do312_day,
    parse_do312_event,
    parse_omr_show_page,
    parse_omr_venue_page,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _page(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


def test_omr_venue_page_lists_its_upcoming_shows() -> None:
    ids = parse_omr_venue_page(_page("omr_venue.html"))
    assert len(ids) > 40
    assert "475629" in ids and "469939" in ids
    assert len(ids) == len(set(ids))


def test_omr_show_page_gives_bands_in_order_and_the_venue() -> None:
    show = parse_omr_show_page(_page("omr_show.html"), "475629")
    assert show is not None and show.structured
    assert [p.name for p in show.performers] == [
        "Zanias",
        "Buzz Kull",
        "Crash Course in Science",
        "Kontravoid",
        "Automelodi",
        "Sleek Teeth",
        "Grave Love",
    ]
    assert show.performers[0].slug == "zanias"
    assert show.starts_at.isoformat() == "2026-10-30T19:00:00-05:00"
    assert show.venue.slug == "thalia-hall" and show.venue.name == "Thalia Hall"
    assert show.venue.latitude and 41 < show.venue.latitude < 42


def test_omr_show_page_without_its_record_is_none() -> None:
    assert parse_omr_show_page(_page("omr_show.html"), "1") is None


def test_do312_day_reads_every_card() -> None:
    shows = parse_do312_day(_page("do312_day.html"))
    assert len(shows) == 25
    s = shows[0]
    assert s.source == "do312" and s.source_id.startswith("/events/2026/10/10/")
    assert s.starts_at.utcoffset() is not None and s.starts_at.date().isoformat() == "2026-10-10"
    assert s.venue.slug and s.venue.name and s.title


def test_do312_event_lists_linked_performers_headliner_first() -> None:
    performers = parse_do312_event(_page("do312_event.html"))
    assert [p.name for p in performers][:3] == ["GORILLAZ", "Little Simz", "Deltron 3030"]


def test_omr_venue_index_lists_every_venue() -> None:
    from musicdata.shows.parse import parse_omr_venue_index

    venues = dict(parse_omr_venue_index(_page("omr_venues_all.html")))
    assert len(venues) > 500
    assert venues["thalia-hall"] == "Thalia Hall" and venues["abbey-pub"] == "Abbey Pub"
    assert "all" not in venues


def test_ticketmaster_events_carry_lineups_local_dates_and_cancellations() -> None:
    import json

    from musicdata.shows.parse import parse_tm_events

    body = json.loads(_page("tm_events.json"))
    shows = parse_tm_events(body)
    assert len(shows) == len(body["_embedded"]["events"])
    s = shows[0]
    assert s.source == "ticketmaster" and s.venue.source == "ticketmaster" and s.venue.slug
    assert s.starts_at.utcoffset().total_seconds() == 0  # the API's UTC dateTime
    assert s.show_date.isoformat() == body["_embedded"]["events"][0]["dates"]["start"]["localDate"]
    assert any(x.cancelled for x in shows)
    titled = [x for x in shows if not x.structured]
    assert titled and all(not x.performers for x in titled)  # split later, from the title
    assert all(x.structured == bool(x.performers) for x in shows)


def test_ticketmaster_presales_are_kept_in_order() -> None:
    import json

    from musicdata.shows.parse import parse_tm_events

    shows = parse_tm_events(json.loads(_page("tm_events.json")))
    with_presales = [s for s in shows if s.presales]
    assert with_presales
    for s in with_presales:
        starts = [p["start"] for p in s.presales]
        assert starts == sorted(starts) and all(p["name"] for p in s.presales)


def test_placeholder_dates_read_as_unknown() -> None:
    from musicdata.shows.parse import _time

    assert _time("1900-01-01T06:00:00Z") is None
    assert _time("2026-10-30T19:00:00.000-05:00").isoformat() == "2026-10-30T19:00:00-05:00"
    assert _time("2026-05-13T15:00:00Z").utcoffset().total_seconds() == 0
