# odyssey-seat-bot

A Telegram bot that watches both Bay Area IMAX 70mm theatres for *The Odyssey* and messages you when a seat you'd actually want opens up.

```
🎟️ Metreon — 2 seats open
K18, K19  ·  center, back rows
Mon Aug 17, 6:00 PM
<booking link>
```

Sold-out showings only free up through cancellations, and those go fast — during development we watched three seats in row K appear and get taken inside an hour. Email alerts are too slow and too easily buried; a push notification isn't.

**For users:** see [HOW-TO-USE.md](HOW-TO-USE.md), written for people who've never opened Telegram.

This is the multi-user successor to [odyssey-seat-watcher](https://github.com/LKalra2094/odyssey-seat-watcher), which did the same thing for one person and one theatre.

## What it watches

| Theatre | Rows | Seats | Showings/day |
|---|---|---|---|
| AMC Metreon 16, SF | A–H, J–N (13) | 437 | 4 |
| Regal Hacienda Crossings, Dublin | A–I (9) | 253 | 4 |

## Signup

Up to six questions, every answer a button. Nobody types anything.

```
Which theatre?
  [ AMC Metreon (SF) ]   [ Regal Hacienda (Dublin) ]   [ Both ]

How far back do you want to sit?
  [ Back rows only ]   Metreon K–N · Regal F–I
  [ Back half ]        Metreon G–N · Regal E–I
  [ Middle & back ]    Metreon E–N · Regal D–I
  Each option includes everything further back.

Where in the row?
  [ Center only ]      Skips the 5 seats at each end
  [ Edges are fine ]   Any seat in the row

When can you go?
  [ Evenings & weekends ]   [ Weekends only ]   [ Anytime ]

How many seats do you need?
  [ Just me ]  [ 2 ]  [ 3 ]  [ 4 or more ]

Do they need to be side by side?          (only asked if more than one)
  [ Yes — together or not at all ]   [ No — we can sit apart ]
```

## Why zones are proportions, not row letters

The obvious design — "alert me about row I or further back" — is broken across theatres, and finding out why was the most useful thing in this project.

**Metreon has no row I.** Its rows run A–H then J–N; theatres routinely skip I so it isn't misread as a 1. **Regal has row I, and it's the back row.** So "row I or beyond" means *the last five rows* at one theatre and *one row* at the other.

So zones are computed per auditorium as a proportion of however many rows it actually has:

| Zone | Rows taken | Metreon (13) | Regal (9) |
|---|---|---|---|
| Back rows only | last 4 | K–N | F–I |
| Back half | last ⌈n/2⌉ | G–N | E–I |
| Middle & back | last ⌈2n/3⌉ | E–N | D–I |

Both end up excluding roughly the front 30%. Add a third theatre and it still works.

**"Five seats from each end" counts position along the row, not seat number.** Metreon's row N is numbered 34..1 but holds only 22 seats; Regal's row H runs 26..5. Counting by number would cut in the wrong place on exactly those rows.

Resulting seat counts:

| | Metreon | Regal |
|---|---|---|
| Back rows + center | 84 | 67 |
| Back rows + edges | 124 | 107 |
| Back half + center | 156 | 89 |
| Back half + edges | 226 | 139 |
| Middle & back + center | 204 | 111 |
| Middle & back + edges | 294 | 171 |

The tightest setting watches 20% of Metreon.

## Detecting seats that are genuinely together

Groups need adjacent seats, and the seat data won't tell you which those are:
`leftNeighbor`/`rightNeighbor` exist but are barely populated — Metreon fills
in 5 of 419, Regal 234 of 243. So adjacency is measured from geometry instead.

Each row has a consistent seat pitch, and an aisle shows up as an outlier:

| | Normal pitch | Aisle gaps |
|---|---|---|
| Metreon | ~17.6 | 82.4, 103.6 |
| Regal | ~40.2 | 156.6 |

A run of available seats breaks at a taken seat *or* at any gap wider than
1.5x that row's median pitch, so a party is never told two seats are together
when there's a walkway between them.

## How it gets the data

Two unauthenticated JSON calls against Fandango's internal API:

```
/napi/theaterMovieShowtimes/<theaterId>?date=YYYY-MM-DD   → showtimes + hash codes
/napi/seatMap/<showtimeHashCode>                          → every seat, live status
```

```json
{ "id": "K32", "row": 11, "column": 12, "type": "standard", "status": "A" }
```

`status` is `A` available / `R` reserved / `H` held. `type` separates `standard` from `wheelchair` and `companion` spaces — which is how those get excluded, since filtering by label would let `WC13` through as "a row after I".

No login, no cookies, no API key, no browser. **Python standard library only** — no pip install, for the Fandango calls and the Telegram API alike.

Going direct to the theatre chains doesn't work: AMC and Regal both sit behind Cloudflare bot management and return `403` to plain HTTP clients. Fandango sells for both, doesn't challenge requests, and serves the chains' real seat maps. Their route table — including `/napi/seatMap/:showtimeHashCode` — is listed in the JavaScript their own site ships to every visitor.

## Design notes

**Polling cost doesn't grow with users.** Each seat map is fetched once per pass and matched against everyone's filters in memory. Five users or five hundred, Fandango sees identical traffic.

**Only showtimes somebody wants are fetched.** If no user picked "anytime", weekday mornings are never requested.

**Showtimes are cached for an hour, and invalidated on `410 Gone`.** Hash codes rotate; when one expires that theatre's list is re-fetched immediately rather than staying blind until the next scheduled refresh.

**Checks are tiered by urgency** — within 24h every 90s, within 3 days every 5 min, beyond that every 15 min, with a 1-second gap between requests.

**Alerts are batched per showing**, capped per user per pass, and de-duplicated per `(user, showtime, seat)` so a second seat in the same showing still pings but the same seat never pings twice.

**It says when it's alive.** A monitor that fails silently is worse than none, because "nothing found" and "broken" look identical from the outside. It logs a heartbeat every 15 minutes and messages the admin on Telegram after five consecutive failed passes.

**Users are capped** (default 100) so alerts stay useful — every extra person is one more competing for the same seat.

## Files

| File | |
|---|---|
| `bot.py` | conversation, watcher loop, entry point |
| `seats.py` | Fandango API, zone maths, time filters |
| `store.py` | SQLite user store |
| `telegram.py` | Bot API wrapper |
| `odyssey-bot.service` | systemd unit for always-on hosting |

## Running it

```bash
python3 bot.py whoami    # confirm your token works
python3 bot.py check     # one pass, printed, sends nothing
python3 bot.py run       # go
```

Create a bot with [@BotFather](https://t.me/botfather) (`/newbot`), then put the token in `config.json`:

```json
{ "bot_token": "...", "admin_chat_id": "..." }
```

`admin_chat_id` is optional — it's where watchdog warnings go when the bot breaks.

For always-on hosting, `odyssey-bot.service` is a systemd unit that restarts on failure and starts at boot. Mine runs on an Oracle Cloud Always Free VM at $0/month. Secrets come from `TG_BOT_TOKEN` and `ADMIN_CHAT_ID` environment variables so nothing sensitive needs to sit in a file.

## What's stored about users

A Telegram chat ID and six preferences. No names, no usernames, no phone numbers, no email — Telegram offers all of them and none are needed. `/stop` deletes the row outright rather than flagging it inactive.

## Being a good citizen

`/napi/` is Fandango's internal API, not a published one. It can change without notice, and when it does this errors loudly rather than going quiet.

Two theatres at roughly one request per second works out to a few thousand requests a day — comparable to a handful of browser tabs. Please don't scale this up into something that hammers them. Automated access sits outside most ticketing sites' terms of service; the polite interval is what keeps this in "a few people refreshing a page" territory.

## License

MIT.
