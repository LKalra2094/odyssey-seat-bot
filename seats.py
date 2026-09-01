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
import statistics
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


def _allowed_row(seats_in_row, position):
    if position != "center":
        return seats_in_row
    if len(seats_in_row) <= 2 * EDGE_SEATS:
        return []
    return seats_in_row[EDGE_SEATS:len(seats_in_row) - EDGE_SEATS]


def _pitch(seats_in_row):
    """Typical gap between neighbouring seats in this row.

    Used to spot aisles: Metreon's normal pitch is ~17.6 with aisles at 82
    and 104; Regal's is ~40.2 with one aisle at 156.6. The seat data's own
    leftNeighbor/rightNeighbor fields are too patchy to rely on — Metreon
    fills in 5 of 419 — so we measure the geometry instead.
    """
    gaps = [b["x"] - a["x"] for a, b in zip(seats_in_row, seats_in_row[1:])]
    return statistics.median(gaps) if gaps else 0.0


def target_rows(rows_by_letter, zone, only=None):
    """Which rows to look at: named rows if given, otherwise the zone.

    `only` is a list of row letters. Any that don't exist in this auditorium
    are simply ignored — Metreon has no row I, Regal has no K.
    """
    if only:
        wanted = {r.upper() for r in only}
        return [r for r in _row_order(rows_by_letter) if r in wanted]
    return zone_rows(rows_by_letter, zone)


def row_letters(theater_id):
    """Row labels that actually exist in this theatre, front to back."""
    shows = showtimes(theater_id)
    if not shows:
        return []
    return _row_order(layout(seat_map(shows[0]["hash"])))


def adjacent_blocks(seat_map_json, zone, position, size, only_rows=None):
    """Runs of `size` or more available seats sitting side by side.

    A run breaks at a taken seat or an aisle, so a group is never told two
    seats are together when there's a walkway between them.
    """
    rows = layout(seat_map_json)
    wanted = set(target_rows(rows, zone, only_rows))
    out = []
    for letter, seats_in_row in rows.items():
        if letter not in wanted:
            continue
        tolerance = _pitch(seats_in_row) * 1.5
        run, prev = [], None
        for seat in _allowed_row(seats_in_row, position):
            gapped = prev is not None and tolerance and (seat["x"] - prev["x"]) > tolerance
            if seat.get("status") != "A" or gapped:
                if len(run) >= size:
                    out.append(run)
                run = []
            if seat.get("status") == "A":
                run.append(seat)
                prev = seat
            else:
                prev = None
        if len(run) >= size:
            out.append(run)
    out.sort(key=lambda r: (_row_order([row_letter(r[0]["id"])])[0], r[0]["x"]))
    return [[s["id"] for s in r] for r in out]


def available_seats(seat_map_json, zone, position, only_rows=None):
    """Seat labels that are free, in the chosen zone, honouring center/edges.

    `position` is 'center' (skip EDGE_SEATS at each end of every row) or
    'any'. Edge trimming counts along the row rather than by seat number,
    because rows have gaps: Metreon's row N is numbered 34..1 but holds 22
    seats, so seat numbers would cut in the wrong place.
    """
    rows = layout(seat_map_json)
    wanted = set(target_rows(rows, zone, only_rows))
    out = []
    for letter, seats_in_row in rows.items():
        if letter not in wanted:
            continue
        out += [s["id"] for s in _allowed_row(seats_in_row, position)
                if s.get("status") == "A"]
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
