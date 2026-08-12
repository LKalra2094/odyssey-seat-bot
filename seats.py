"""
seats.py — everything about finding seats.

Talks to Fandango's internal JSON API, works out which seats sit in which
zone of a given auditorium, and decides whether a showtime falls inside a
user's chosen hours.

Deliberately knows nothing about Telegram or users.
"""

import json
import math
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from zoneinfo import ZoneInfo

# --- the two theatres ------------------------------------------------------

THEATERS = {
    "AANEM": {"name": "AMC Metreon", "short": "Metreon",
              "tz": ZoneInfo("America/Los_Angeles")},
    "AAOPK": {"name": "Regal Hacienda Crossings", "short": "Regal Dublin",
              "tz": ZoneInfo("America/Los_Angeles")},
}

MOVIE_MATCH = "odyssey"
FORMAT_MATCH = "IMAX 70MM"        # exact filmFormat tag; excludes laser IMAX
HORIZON_DAYS = 14

# Seats this far in from each end of a row count as "edge" seats.
EDGE_SEATS = 5

BASE = "https://www.fandango.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

SHOWTIME_CACHE_SECONDS = 3600     # re-ask what's playing once an hour


class Gone(Exception):
    """A showtime hash has expired. Its theatre's list needs re-fetching."""


# --- http ------------------------------------------------------------------

def _get(path):
    req = urllib.request.Request(BASE + path, headers={
        "Accept": "application/json",
        "User-Agent": UA,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{BASE}/",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            raise Gone(f"HTTP {e.code}") from e
        raise


# --- showtimes -------------------------------------------------------------

_cache = {}          # theater_id -> (fetched_at, [showtime, ...])


def _parse_start(raw, tz):
    """'2026-08-11+10:00' -> aware datetime in the theatre's timezone."""
    day, clock = str(raw).split("+")
    return datetime.strptime(f"{day} {clock}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)


def _fetch_showtimes(theater_id):
    tz = THEATERS[theater_id]["tz"]
    today = datetime.now(tz).date()
    out, seen = [], set()
    for i in range(HORIZON_DAYS):
        date = (today + timedelta(days=i)).isoformat()
        try:
            data = _get(f"/napi/theaterMovieShowtimes/{theater_id}?date={date}")
        except (Gone, urllib.error.URLError, json.JSONDecodeError):
            continue
        for movie in data.get("viewModel", {}).get("movies", []):
            if MOVIE_MATCH not in movie.get("title", "").lower():
                continue
            for variant in movie.get("variants", []):
                for group in variant.get("amenityGroups", []):
                    for st in group.get("showtimes", []):
                        tags = {f.get("filterName")
                                for f in st.get("filmFormat") or []}
                        if FORMAT_MATCH not in tags or st.get("expired"):
                            continue
                        h = st.get("showtimeHashCode")
                        if not h or h in seen:
                            continue
                        try:
                            start = _parse_start(st["ticketingDate"], tz)
                        except (KeyError, ValueError):
                            continue
                        seen.add(h)
                        out.append({
                            "theater": theater_id,
                            "hash": h,
                            "start": start,
                            "url": st.get("ticketingJumpPageURL"),
                        })
        time.sleep(0.5)
    return sorted(out, key=lambda s: s["start"])


def showtimes(theater_id, force=False):
    """Cached showtime list. `force` re-fetches (used when a hash goes stale)."""
    hit = _cache.get(theater_id)
    if hit and not force and time.time() - hit[0] < SHOWTIME_CACHE_SECONDS:
        return hit[1]
    fresh = _fetch_showtimes(theater_id)
    # Keep the stale list if a refresh comes back empty — better than going blind.
    if not fresh and hit:
        return hit[1]
    _cache[theater_id] = (time.time(), fresh)
    return fresh


def invalidate(theater_id):
    _cache.pop(theater_id, None)


def seat_map(hash_code):
    return _get(f"/napi/seatMap/{hash_code}")


# --- reading an auditorium -------------------------------------------------

def row_letter(seat_id):
    m = re.match(r"([A-Za-z]+)", str(seat_id) or "")
    return m.group(1).upper() if m else ""


def _row_order(rows):
    """Sort row labels front-to-back. Multi-letter rows (AA) come after Z."""
    return sorted(rows, key=lambda r: (len(r), r))


def layout(seat_map_json):
    """{row_letter: [seat, ...] ordered left-to-right}, standard seats only.

    Wheelchair and companion spaces are dropped here rather than by label,
    because they're labelled 'WC…' which would otherwise sort after 'I'.
    """
    rows = {}
    for s in seat_map_json.get("seats", []):
        if s.get("type") != "standard":
            continue
        rows.setdefault(row_letter(s.get("id")), []).append(s)
    for r in rows:
        rows[r].sort(key=lambda s: s.get("x", 0))
    rows.pop("", None)
    return rows


ZONES = ("back_rows", "back_half", "middle_back")

ZONE_LABEL = {
    "back_rows": "back rows",
    "back_half": "back half",
    "middle_back": "middle & back",
}


def zone_rows(rows_by_letter, zone):
    """Which row letters belong to a zone, as a proportion of this auditorium.

    Computed per theatre rather than hardcoded: Metreon has 13 rows and no
    row I, Regal has 9 and row I is its last. Letters are not portable.
    """
    ordered = _row_order(rows_by_letter)
    n = len(ordered)
    if n == 0:
        return []
    if zone == "back_rows":
        take = min(4, n)
    elif zone == "back_half":
        take = math.ceil(n / 2)
    else:
        take = math.ceil(n * 2 / 3)
    return ordered[-take:]


def available_seats(seat_map_json, zone, position):
    """Seat labels that are free, in the chosen zone, honouring center/edges.

    `position` is 'center' (skip EDGE_SEATS at each end of every row) or
    'any'. Edge trimming counts along the row rather than by seat number,
    because rows have gaps: Metreon's row N is numbered 34..1 but holds 22
    seats, so seat numbers would cut in the wrong place.
    """
    rows = layout(seat_map_json)
    wanted = set(zone_rows(rows, zone))
    out = []
    for letter, seats in rows.items():
        if letter not in wanted:
            continue
        row = seats
        if position == "center":
            row = row[EDGE_SEATS:len(row) - EDGE_SEATS] if len(row) > 2 * EDGE_SEATS else []
        out += [s["id"] for s in row if s.get("status") == "A"]
    return sorted(out, key=lambda sid: (_row_order([row_letter(sid)])[0], sid))


# --- when someone can actually go ------------------------------------------

TIME_CHOICES = ("evenings_weekends", "weekends", "anytime")

TIME_LABEL = {
    "evenings_weekends": "evenings & weekends",
    "weekends": "weekends only",
    "anytime": "anytime",
}


def time_matches(start, choice):
    weekend = start.weekday() >= 5          # Sat, Sun
    if choice == "anytime":
        return True
    if choice == "weekends":
        return weekend
    return weekend or start.hour >= 17      # weeknights from 5pm
