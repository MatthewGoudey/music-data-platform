"""Show pages → plain records. Pure functions, no I/O; each source's markup stays here.

Oh My Rockness (primary):
  venue page  → schema.org MusicEvent JSON-LD for every upcoming show: id, url, name.
  show page   → its own record embedded as JSON (`data-show`): bands in order with
                slugs, start time with offset, the venue with address and coordinates.
do312 (secondary):
  day listing → schema.org microdata per event card: title, venue, start time.
  event page  → performers as /artists/ links when the site has them; otherwise the
                title is split by musicdata.identity.split_lineup.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class Venue:
    source: str
    slug: str
    name: str
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    website: str | None = None


@dataclass(frozen=True)
class Performer:
    name: str
    slug: str | None = None


@dataclass
class Show:
    source: str
    source_id: str
    url: str
    starts_at: datetime
    title: str
    venue: Venue
    performers: list[Performer] = field(default_factory=list)
    structured: bool = False  # performers came from the site, not from splitting a title
    tickets_url: str | None = None
    on_sale_at: datetime | None = None
    price: str | None = None
    local_date: date | None = None  # when starts_at is not in the venue's own offset
    cancelled: bool = False

    @property
    def show_date(self) -> date:
        """The venue's calendar date: what one night means across sources."""
        return self.local_date or self.starts_at.date()


def _float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _time(value: str | None) -> datetime | None:
    """ISO times as both sites write them: "2026-10-30T19:00:00.000-05:00", "…-0500"."""
    if not value:
        return None
    v = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value.strip())
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def _text(value: str | None) -> str:
    return " ".join(html.unescape(value or "").split())


# --- Oh My Rockness -------------------------------------------------------------------

OMR_SHOW_ID = re.compile(r"/shows/(\d+)")
_LD_JSON = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
_DATA_SHOWS = re.compile(r'data-shows?="([^"]+)"')  # data-show: the page's own record


_VENUE_INDEX = re.compile(r'<a class="omrlink" href="/venues/([a-z0-9-]+)">([^<]+)</a>')


def parse_omr_venue_index(page: str) -> list[tuple[str, str]]:
    """(slug, name) for every venue on /venues/all, past ones included."""
    seen: dict[str, str] = {}
    for slug, name in _VENUE_INDEX.findall(page):
        if slug != "all":
            seen.setdefault(slug, _text(name))
    return list(seen.items())


def parse_omr_venue_page(page: str) -> list[str]:
    """Ids of the upcoming shows a venue page lists, in page order."""
    ids: list[str] = []
    for block in _LD_JSON.findall(page):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        for event in data if isinstance(data, list) else [data]:
            if not isinstance(event, dict) or event.get("@type") != "MusicEvent":
                continue
            m = OMR_SHOW_ID.search(event.get("url") or "")
            if m and m.group(1) not in ids:
                ids.append(m.group(1))
    return ids


def _omr_records(page: str) -> list[dict]:
    records: list[dict] = []
    for block in _DATA_SHOWS.findall(page):
        try:
            data = json.loads(html.unescape(block))
        except json.JSONDecodeError:
            continue
        records += [r for r in (data if isinstance(data, list) else [data]) if isinstance(r, dict)]
    return records


def parse_omr_show_page(page: str, show_id: str) -> Show | None:
    """The show's own embedded record; None when the page does not carry it."""
    record = next((r for r in _omr_records(page) if str(r.get("id")) == str(show_id)), None)
    if record is None:
        return None
    starts_at = _time(record.get("starts_at"))
    v = record.get("venue") or {}
    if starts_at is None or not v.get("slug"):
        return None
    performers = [
        Performer(name=_text(b.get("name")), slug=b.get("slug"))
        for b in record.get("cached_bands") or []
        if _text(b.get("name"))
    ]
    return Show(
        source="omr",
        source_id=str(show_id),
        url=f"https://chicago.ohmyrockness.com/shows/{show_id}",
        starts_at=starts_at,
        title=", ".join(p.name for p in performers),
        venue=Venue(
            source="omr",
            slug=v["slug"],
            name=_text(v.get("name")),
            address=_text(v.get("full_address")) or None,
            latitude=_float(v.get("latitude")),
            longitude=_float(v.get("longitude")),
            website=v.get("website") or None,
        ),
        performers=performers,
        structured=True,
        tickets_url=record.get("tickets_url") or None,
        on_sale_at=_time(record.get("on_sale_at")),
        price=record.get("price") or None,
    )


# --- Ticketmaster (Discovery API) -----------------------------------------------------


def _tm_price(event: dict) -> str | None:
    ranges = [r for r in event.get("priceRanges") or [] if r.get("min") is not None]
    if not ranges:
        return None
    low = min(float(r["min"]) for r in ranges)
    high = max(float(r.get("max") or r["min"]) for r in ranges)
    if high <= 0:
        return None
    return f"${low:.0f}" if low == high else f"${low:.0f}–${high:.0f}"


def parse_tm_events(body: dict) -> list[Show]:
    """Events from one Discovery API page. Attractions are the lineup when present; a title
    without them is split later like any listing title."""
    shows: list[Show] = []
    for e in (body.get("_embedded") or {}).get("events") or []:
        start = (e.get("dates") or {}).get("start") or {}
        starts_at = _time((start.get("dateTime") or "").replace("Z", "+00:00"))
        venues = (e.get("_embedded") or {}).get("venues") or []
        if starts_at is None or not venues or not venues[0].get("id"):
            continue
        v = venues[0]
        location = v.get("location") or {}
        address = ", ".join(
            x
            for x in (
                (v.get("address") or {}).get("line1"),
                (v.get("city") or {}).get("name"),
                " ".join(
                    filter(None, ((v.get("state") or {}).get("stateCode"), v.get("postalCode")))
                ),
            )
            if x
        )
        attractions = (e.get("_embedded") or {}).get("attractions") or []
        local = start.get("localDate")
        shows.append(
            Show(
                source="ticketmaster",
                source_id=e["id"],
                url=e.get("url") or "",
                starts_at=starts_at,
                title=_text(e.get("name")),
                venue=Venue(
                    source="ticketmaster",
                    slug=v["id"],
                    name=_text(v.get("name")),
                    address=_text(address) or None,
                    latitude=_float(location.get("latitude")),
                    longitude=_float(location.get("longitude")),
                    website=v.get("url") or None,
                ),
                performers=[
                    Performer(name=_text(a.get("name")), slug=a.get("id"))
                    for a in attractions
                    if _text(a.get("name"))
                ],
                structured=bool(attractions),
                tickets_url=e.get("url") or None,
                on_sale_at=_time(
                    (
                        ((e.get("sales") or {}).get("public") or {}).get("startDateTime") or ""
                    ).replace("Z", "+00:00")
                ),
                price=_tm_price(e),
                local_date=date.fromisoformat(local) if local else None,
                cancelled=((e.get("dates") or {}).get("status") or {}).get("code") == "cancelled",
            )
        )
    return shows


# --- do312 ----------------------------------------------------------------------------

_CARD_SPLIT = re.compile(r'<div class="ds-listing event-card')
_PERMALINK = re.compile(r'data-permalink="([^"]+)"')
_TITLE = re.compile(r'class="ds-listing-event-title-text" itemprop="name">([^<]*)<')
_VENUE = re.compile(r'<a href="/venues/([^"]+)" itemprop="url"><span itemprop="name">([^<]*)<')
_META = re.compile(r'<meta itemprop="(\w+)"(?: datetime="[^"]*")? content="([^"]*)"')
_ARTIST_LINK = re.compile(r'href="/artists/([^"]+)"[^>]*>\s*(?:<[^>]+>\s*)*([^<]{1,120})<')

DO312_PAGE_SIZE = 25


def parse_do312_day(page: str) -> list[Show]:
    """Every event card on a do312 listing page (performers not yet split)."""
    shows: list[Show] = []
    for card in _CARD_SPLIT.split(page)[1:]:
        permalink, title, venue = _PERMALINK.search(card), _TITLE.search(card), _VENUE.search(card)
        if not (permalink and title and venue) or not permalink.group(1).startswith("/events/"):
            continue
        meta = dict(_META.findall(card))
        starts_at = _time(meta.get("startDate"))
        if starts_at is None:
            continue
        address = ", ".join(
            x
            for x in (
                meta.get("streetAddress"),
                meta.get("addressLocality"),
                " ".join(filter(None, (meta.get("addressRegion"), meta.get("postalCode")))),
            )
            if x
        )
        shows.append(
            Show(
                source="do312",
                source_id=permalink.group(1),
                url="https://do312.com" + permalink.group(1),
                starts_at=starts_at,
                title=_text(title.group(1)),
                venue=Venue(
                    source="do312",
                    slug=venue.group(1),
                    name=_text(venue.group(2)),
                    address=_text(address) or None,
                    latitude=_float(meta.get("latitude")),
                    longitude=_float(meta.get("longitude")),
                ),
            )
        )
    return shows


def parse_do312_event(page: str) -> list[Performer]:
    """Performers an event page links, headliner first; empty when it links none."""
    out: list[Performer] = []
    seen: set[str] = set()
    for slug, name in _ARTIST_LINK.findall(page):
        text = _text(name)
        if not text or text.lower() == "profile" or slug in seen:
            continue
        seen.add(slug)
        out.append(Performer(name=text, slug=slug))
    return out
