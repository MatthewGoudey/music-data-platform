"""Google Routes API: travel time from one origin to many venues (computeRouteMatrix).

The key rides in a header, never the URL, so it cannot appear in an error or a log line.
Transit matrices allow 100 elements a request, so destinations go in batches of 100.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
FIELDS = "originIndex,destinationIndex,duration,distanceMeters,condition,status"
BATCH = 100


@dataclass(frozen=True)
class Place:
    """A venue to reach: coordinates when known, else an address line."""

    latitude: float | None = None
    longitude: float | None = None
    address: str | None = None

    def waypoint(self) -> dict:
        if self.latitude is not None and self.longitude is not None:
            return {
                "location": {"latLng": {"latitude": self.latitude, "longitude": self.longitude}}
            }
        return {"address": self.address}


@dataclass(frozen=True)
class Leg:
    minutes: int | None
    meters: int | None
    found: bool


class RoutesClient:
    def __init__(self, api_key: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._key = api_key
        self._http = client or httpx.AsyncClient(timeout=60)
        self.elements = 0

    async def __aenter__(self) -> RoutesClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    async def matrix(
        self, origin: str, places: list[Place], mode: str, departure: str | None = None
    ) -> list[Leg]:
        """One Leg per place, in order. `mode` is TRANSIT or WALK; `departure` is an RFC 3339
        time for TRANSIT."""
        legs: list[Leg] = []
        for start in range(0, len(places), BATCH):
            batch = places[start : start + BATCH]
            body: dict[str, object] = {
                "origins": [{"waypoint": {"address": origin}}],
                "destinations": [{"waypoint": p.waypoint()} for p in batch],
                "travelMode": mode,
            }
            if departure:
                body["departureTime"] = departure
            r = await self._http.post(
                URL,
                json=body,
                headers={"X-Goog-Api-Key": self._key, "X-Goog-FieldMask": FIELDS},
            )
            if r.status_code != 200:
                body = r.json() if r.content else {}
                body = (
                    body[0] if isinstance(body, list) and body else body
                )  # a matrix answers in a list
                detail = body.get("error", {}).get("message", "") if isinstance(body, dict) else ""
                raise RuntimeError(f"Routes API {r.status_code}: {detail[:200]}")
            self.elements += len(batch)
            found: dict[int, Leg] = {}
            for el in r.json():
                ok = el.get("condition") == "ROUTE_EXISTS"
                seconds = str(el.get("duration", "")).rstrip("s")
                found[int(el.get("destinationIndex", 0))] = Leg(
                    minutes=round(int(seconds) / 60) if ok and seconds.isdigit() else None,
                    meters=el.get("distanceMeters") if ok else None,
                    found=ok,
                )
            legs += [found.get(i, Leg(None, None, False)) for i in range(len(batch))]
        return legs
