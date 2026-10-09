"""Venue travel times: the departure, the sensible way to go, and the Routes client."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from musicdata.clients.routes import Place, RoutesClient
from musicdata.shows.travel import CHICAGO, place_of, show_night, travel_line


def test_transit_is_timed_for_the_coming_friday_evening() -> None:
    thursday = datetime(2026, 10, 8, 15, tzinfo=UTC)
    at = show_night(thursday)
    assert (at.weekday(), at.hour, at.minute, at.day) == (4, 18, 30, 9)
    friday_night = datetime(2026, 10, 10, 2, tzinfo=UTC)  # Friday 9 PM in Chicago
    assert show_night(friday_night).astimezone(CHICAGO).day == 16


def test_the_sensible_way_to_get_there() -> None:
    assert travel_line(9, 12) == "12 min walk"  # Thalia Hall: just walk
    assert travel_line(14, 25) == "25 min walk · 14 min CTA"
    assert travel_line(32, 95) == "32 min CTA"
    assert travel_line(30, 28) == "28 min walk"  # the train is no faster
    assert travel_line(None, 70) == "70 min walk"
    assert travel_line(None, None) is None


def test_a_venue_is_reached_by_coordinates_or_address() -> None:
    assert place_of("Empty Bottle", None, 41.9, -87.68).waypoint()["location"]
    assert place_of("Empty Bottle", None, None, None).waypoint() == {
        "address": "Empty Bottle, Chicago, IL"
    }


async def test_the_matrix_keeps_order_and_marks_missing_routes() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers["X-Goog-Api-Key"]
        body = json.loads(request.content)
        assert body["travelMode"] == "TRANSIT" and body["departureTime"]
        return httpx.Response(
            200,
            json=[
                {"originIndex": 0, "destinationIndex": 1, "condition": "ROUTE_NOT_FOUND"},
                {
                    "originIndex": 0,
                    "destinationIndex": 0,
                    "condition": "ROUTE_EXISTS",
                    "duration": "1500s",
                    "distanceMeters": 5200,
                },
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with RoutesClient("k-123", client=client) as routes:
        legs = await routes.matrix(
            "home", [Place(address="A"), Place(address="B")], "TRANSIT", "2026-10-09T23:30:00Z"
        )
    assert seen["key"] == "k-123"
    assert (legs[0].minutes, legs[0].found, legs[1].found) == (25, True, False)
