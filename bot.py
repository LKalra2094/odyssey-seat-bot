#!/usr/bin/env python3
"""
bot.py — The Odyssey 70mm seat bot.

Two jobs running side by side:

  * a conversation thread, listening for people signing up on Telegram
  * a watcher thread, polling Fandango and messaging whoever matches

    python3 bot.py run       # both threads, forever
    python3 bot.py check     # one watcher pass, printed, sends nothing
    python3 bot.py whoami    # confirm the token works

Configuration lives in config.json (or TG_BOT_TOKEN / ADMIN_CHAT_ID env vars).
Standard library only.
"""

import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timezone

import seats
import store
from telegram import Blocked, Telegram, keyboard

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")

MAX_USERS = 100                  # raise this when you want more people on
TICK_SECONDS = 30                # how often the watcher wakes up
HEARTBEAT_SECONDS = 900          # log "still alive" this often
REQUEST_GAP = 1.0                # seconds between Fandango requests
FANOUT_GAP = 0.3                 # seconds between messages in one burst
MAX_ALERTS_PER_USER_PER_PASS = 3 # never flood someone in a single pass
WATCHDOG_FAILURES = 5            # consecutive bad passes before shouting


def log(msg):
    print(f"[{datetime.now().strftime('%m-%d %H:%M:%S')}] {msg}", flush=True)


# --- how often to re-check a showtime, by how close it is ------------------

def cadence_for(hours_away):
    if hours_away <= 24:
        return 90
    if hours_away <= 72:
        return 300
    return 900


# ---------------------------------------------------------------------------
# The conversation
# ---------------------------------------------------------------------------

WELCOME = (
    "I watch The Odyssey in IMAX 70mm at the two Bay Area theatres that show "
    "it, and message you the moment a good seat opens up.\n\n"
    "Tickets are almost always sold out — these are cancellations, and they go "
    "fast. A few quick questions and you're set."
)

QUESTIONS = {
    "theater": (
        "Which theatre?",
        [[("AMC Metreon (SF)", "theater:AANEM")],
         [("Regal Hacienda (Dublin)", "theater:AAOPK")],
         [("Both", "theater:both")]],
    ),
    "row_zone": (
        "How far back do you want to sit?\n"
        "Each option includes everything further back.",
        [[("Back rows only  ·  Metreon K–N · Regal F–I", "row_zone:back_rows")],
         [("Back half  ·  Metreon G–N · Regal E–I", "row_zone:back_half")],
         [("Middle & back  ·  Metreon E–N · Regal D–I", "row_zone:middle_back")]],
    ),
    "row_position": (
        "Where in the row?",
        [[("Center only  ·  skips 5 seats at each end", "row_position:center")],
         [("Edges are fine  ·  any seat in the row", "row_position:any")]],
    ),
    "times": (
        "When could you actually go?",
        [[("Evenings & weekends", "times:evenings_weekends")],
         [("Weekends only", "times:weekends")],
         [("Anytime", "times:anytime")]],
    ),
    "party_size": (
        "How many seats do you need?",
        [[("Just me", "party_size:1"), ("2", "party_size:2")],
         [("3", "party_size:3"), ("4 or more", "party_size:4")]],
    ),
    "together": (
        "Do they need to be side by side?",
        [[("Yes — together or not at all", "together:yes")],
         [("No — we can sit apart", "together:no")]],
    ),
}

ORDER = ["theater", "row_zone", "row_position", "times", "party_size", "together"]

# First line of each question, for the "you picked X" confirmation.
QUESTION_TITLE = {k: v[0].split("\n")[0] for k, v in QUESTIONS.items()}

# 'theater:AANEM' -> 'AMC Metreon (SF)'. The bit before the '·' is the label;
# the rest is explanatory detail we don't need to echo back.
CHOICE_LABEL = {data: text.split("·")[0].strip()
                for _, rows in QUESTIONS.values()
                for row in rows for text, data in row}


def party_of(user):
    """How many seats they need, and whether they must be side by side."""
    try:
        size = int(user.get("party_size") or 1)
    except (TypeError, ValueError):
        size = 1
    return max(1, size), (user.get("together") == "yes" and size > 1)


def describe(user):
    where = ("both theatres" if user["theater"] == "both"
             else seats.THEATERS[user["theater"]]["name"])
    pos = "center seats only" if user["row_position"] == "center" else "any seat in the row"
    size, together = party_of(user)
    if size == 1:
        party = "1 seat"
    else:
        party = (f"{size}{'+' if size >= 4 else ''} seats, "
                 f"{'side by side' if together else 'happy to sit apart'}")
    return (f"{where}\n"
            f"{seats.ZONE_LABEL[user['row_zone']].capitalize()} or further back, {pos}\n"
            f"{seats.TIME_LABEL[user['times']].capitalize()}\n"
            f"{party}")


class Conversation:
    def __init__(self, tg):
        self.tg = tg

    def ask(self, chat_id, step):
        text, rows = QUESTIONS[step]
        self.tg.send(chat_id, text, keyboard(rows))
        store.upsert(chat_id, step=step)

    def begin(self, chat_id, intro):
        store.upsert(chat_id, status="signup")
        self.tg.send(chat_id, intro)
        self.ask(chat_id, "theater")

    def start(self, chat_id):
        user = store.get(chat_id)
        if not user and store.count() >= MAX_USERS:
            self.tg.send(
                chat_id,
                "Sorry — the bot is full right now (it's capped so alerts stay "
                "useful). Try again in a few days.")
            return
        if user and user["status"] in ("active", "paused"):
            self.tg.send(chat_id,
                         "You're already set up.\n\n" + describe(user) +
                         "\n\n/reset to change any of this  ·  /status  ·  /stop")
            return
        self.begin(chat_id, WELCOME)

    def reset(self, chat_id):
        user = store.get(chat_id)
        if not user:
            self.begin(chat_id, WELCOME)
            return
        self.begin(chat_id,
                   "Right now I'm watching:\n\n" + describe(user) +
                   "\n\nLet's go through the questions again to replace that.")

    def answer(self, chat_id, field, value):
        store.upsert(chat_id, **{field: value})
        nxt = ORDER.index(field) + 1
        if field == "party_size" and value == "1":
            # Nobody sitting alone needs asking whether they'll sit together.
            store.upsert(chat_id, together="na")
            nxt = len(ORDER)
        if nxt < len(ORDER):
            self.ask(chat_id, ORDER[nxt])
            return
        store.upsert(chat_id, status="active", step=None)
        user = store.get(chat_id)
        self.tg.send(
            chat_id,
            "Done — I'm watching.\n\n" + describe(user) +
            "\n\nCould be hours, could be days. Good seats are rare, so silence "
            "is normal. I'll message you the moment one appears.\n\n"
            "/status  what I'm watching  ·  /reset  ·  /pause  ·  /stop  ·  /test")

    def command(self, chat_id, cmd):
        user = store.get(chat_id)
        if cmd == "/start":
            self.start(chat_id)
        elif cmd == "/reset":
            self.reset(chat_id)
        elif cmd == "/status":
            if not user or user["status"] not in ("active", "paused"):
                self.tg.send(chat_id, "You're not signed up. Send /start.")
            else:
                state = "Paused" if user["status"] == "paused" else "Watching"
                self.tg.send(chat_id, f"{state}:\n\n{describe(user)}")
        elif cmd == "/pause":
            if user and user["status"] == "active":
                store.upsert(chat_id, status="paused")
                self.tg.send(chat_id, "Paused. Send /resume when you want alerts again.")
            else:
                self.tg.send(chat_id, "Nothing to pause.")
        elif cmd == "/resume":
            if user and user["status"] == "paused":
                store.upsert(chat_id, status="active")
                self.tg.send(chat_id, "Watching again.")
            else:
                self.tg.send(chat_id, "Nothing to resume. Send /start.")
        elif cmd == "/stop":
            store.forget(chat_id)
            self.tg.send(chat_id, "Stopped, and I've deleted your details. "
                                  "Send /start if you ever want back in.")
        elif cmd == "/test":
            self.tg.send(chat_id,
                         "🎟️ Metreon — 2 together\n"
                         "K18, K19  ·  center, back rows\n"
                         "Mon Aug 17, 6:00 PM\n"
                         "(this is a test — no seats have actually opened)")
        else:
            self.tg.send(
                chat_id,
                "Commands:\n/start  sign up\n/status  what I'm watching\n"
                "/reset  change my answers\n/pause  and  /resume\n"
                "/stop  leave and delete my details\n/test  send me a fake alert")

    def handle(self, update):
        if "callback_query" in update:
            q = update["callback_query"]
            msg = q.get("message") or {}
            chat_id = (msg.get("chat") or {}).get("id")
            msg_id = msg.get("message_id")
            field, _, value = q.get("data", "").partition(":")
            if not chat_id or field not in QUESTIONS or not value:
                self.tg.ack(q["id"])
                return
            user = store.get(chat_id)
            # Telegram leaves old buttons tappable forever. Only honour the
            # question we're actually on, or a double-tap asks the next
            # question twice and the whole flow doubles up.
            if not user or user["step"] != field:
                self.tg.ack(q["id"], "Already answered — /reset to change your answers")
                if msg_id:
                    self.tg.edit(chat_id, msg_id,
                                 f"{QUESTION_TITLE[field]}\n(already answered)")
                return
            self.tg.ack(q["id"])
            if msg_id:
                self.tg.edit(
                    chat_id, msg_id,
                    f"{QUESTION_TITLE[field]}\n✓ {CHOICE_LABEL.get(q['data'], value)}")
            self.answer(chat_id, field, value)
            return
        msg = update.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if not chat_id:
            return
        if text.startswith("/"):
            self.command(chat_id, text.split()[0].split("@")[0].lower())
        else:
            user = store.get(chat_id)
            if user and user["status"] == "signup" and user["step"]:
                self.ask(chat_id, user["step"])     # re-ask, they typed instead
            else:
                self.command(chat_id, "/help")


def conversation_loop(tg):
    convo = Conversation(tg)
    while True:
        try:
            for update in tg.updates():
                try:
                    convo.handle(update)
                except Blocked:
                    pass
                except Exception:
                    log("conversation error:\n" + traceback.format_exc())
        except Exception as e:
            log(f"getUpdates failed: {e}")
            time.sleep(10)


# ---------------------------------------------------------------------------
# The watcher
# ---------------------------------------------------------------------------

def wanted_showtimes(users):
    """Every showtime at least one active user could care about.

    Nobody wants weekday mornings unless somebody picked "anytime", so this
    keeps us from fetching seat maps no living person is waiting on.
    """
    theaters = set()
    for u in users:
        theaters.update(seats.THEATERS if u["theater"] == "both" else [u["theater"]])
    out = []
    now = datetime.now(timezone.utc)
    for tid in theaters:
        for show in seats.showtimes(tid):
            if show["start"] <= now:
                continue
            if any(u["times"] and seats.time_matches(show["start"], u["times"])
                   and (u["theater"] in ("both", tid)) for u in users):
                out.append(show)
    return sorted(out, key=lambda s: s["start"])


def alert_text(show, groups, user):
    """`groups` is a list of seat-id lists. One group per side-by-side block
    when the user wants to sit together, otherwise a single group."""
    t = seats.THEATERS[show["theater"]]
    when = show["start"].strftime("%a %b %-d, %-I:%M %p")
    pos = "center" if user["row_position"] == "center" else "any"
    _, together = party_of(user)
    flat = [s for g in groups for s in g]
    n = len(flat)
    if together:
        shown = "   /   ".join(", ".join(g) for g in groups[:3])
        if len(groups) > 3:
            shown += f"   /   +{len(groups) - 3} more blocks"
        headline = (f"{len(groups[0])} together" if len(groups) == 1
                    else f"{len(groups)} blocks together")
    else:
        shown = ", ".join(flat[:10]) + (f", +{n - 10} more" if n > 10 else "")
        headline = f"{n} seat{'s' if n != 1 else ''} open"
    return (f"🎟️ {t['short']} — {headline}\n"
            f"{shown}  ·  {pos}, {seats.ZONE_LABEL[user['row_zone']]}\n"
            f"{when}\n{show['url'] or ''}")


def matching_groups(sm, user):
    """Seats worth telling this user about, grouped for the message.

    Returns [] when there aren't enough — a party of three hearing about a
    single seat is just noise.
    """
    size, together = party_of(user)
    zone, pos = user["row_zone"], user["row_position"]
    if together:
        return seats.adjacent_blocks(sm, zone, pos, size)
    free = seats.available_seats(sm, zone, pos)
    return [free] if len(free) >= size else []


def watcher_pass(tg, due, dry_run=False):
    """One sweep. Returns (alerts_sent, showtimes_considered, maps_fetched)."""
    users = store.active_users()
    if not users and not dry_run:
        return 0, 0, 0

    shows = wanted_showtimes(users if users else [
        {"theater": "both", "times": "anytime", "row_zone": "back_half",
         "row_position": "any"}])
    store.prune([s["hash"] for s in shows])

    now = datetime.now(timezone.utc)
    sent = fetched = 0
    per_user = {}
    for show in shows:
        hours = (show["start"] - now).total_seconds() / 3600
        if not dry_run and time.time() < due.get(show["hash"], 0):
            continue
        due[show["hash"]] = time.time() + cadence_for(hours)
        try:
            sm = seats.seat_map(show["hash"])
        except seats.Gone:
            seats.invalidate(show["theater"])
            log(f"  hash expired for {show['start']:%a %-I:%M%p} "
                f"({show['theater']}) — refreshing that theatre")
            continue
        except Exception as e:
            log(f"  seat map failed {show['start']:%a %-I:%M%p}: {e}")
            continue
        fetched += 1
        time.sleep(REQUEST_GAP)

        for user in users:
            if user["theater"] not in ("both", show["theater"]):
                continue
            if not seats.time_matches(show["start"], user["times"]):
                continue
            groups = matching_groups(sm, user)
            if not groups or dry_run:
                continue
            # A block counts as news if any seat in it is new to this user;
            # we then send the whole block so it still reads as "3 together".
            groups = [g for g in groups
                      if store.unsent(user["chat_id"], show["hash"], g)]
            if not groups:
                continue
            if per_user.get(user["chat_id"], 0) >= MAX_ALERTS_PER_USER_PER_PASS:
                continue
            fresh = [s for g in groups for s in g]
            try:
                tg.send(user["chat_id"], alert_text(show, groups, user))
                store.mark_sent(user["chat_id"], show["hash"], fresh)
                per_user[user["chat_id"]] = per_user.get(user["chat_id"], 0) + 1
                sent += 1
                time.sleep(FANOUT_GAP)
            except Blocked:
                log(f"  {user['chat_id']} blocked the bot — removing")
                store.forget(user["chat_id"])
            except Exception as e:
                log(f"  send failed for {user['chat_id']}: {e}")

        if dry_run:
            report_dry(show, sm)
    return sent, len(shows), fetched


def report_dry(show, sm):
    t = seats.THEATERS[show["theater"]]["short"]
    when = show["start"].strftime("%a %b %-d %-I:%M%p")
    rows = seats.layout(sm)
    free = sum(1 for r in rows.values() for s in r if s.get("status") == "A")
    bits = []
    for zone in seats.ZONES:
        for pos in ("center", "any"):
            n = len(seats.available_seats(sm, zone, pos))
            if n:
                bits.append(f"{seats.ZONE_LABEL[zone]}/{pos}={n}")
    print(f"  {t:<8} {when:<20} {free:>3} free  "
          + (", ".join(bits) if bits else "nothing in any zone"))


def watcher_loop(tg, admin_chat):
    due, failures, last_beat = {}, 0, 0.0
    log(f"Watcher started. {store.count()} users registered.")
    while True:
        try:
            sent, considered, fetched = watcher_pass(tg, due)
            if failures >= WATCHDOG_FAILURES and admin_chat:
                try:
                    tg.send(admin_chat, "✅ Seat bot recovered — polling again.")
                except Exception:
                    pass
            failures = 0
            if time.time() - last_beat > HEARTBEAT_SECONDS:
                log(f"alive — {store.count()} users, {considered} showtimes "
                    f"tracked, {fetched} seat maps this pass, {sent} alerts sent")
                last_beat = time.time()
        except Exception:
            failures += 1
            log(f"pass failed ({failures}):\n" + traceback.format_exc())
            if failures == WATCHDOG_FAILURES and admin_chat:
                try:
                    tg.send(admin_chat,
                            f"⚠️ Seat bot has failed {failures} passes in a row. "
                            f"Fandango may have changed something. Check the logs.")
                except Exception:
                    pass
            time.sleep(min(60 * failures, 900))
            continue
        time.sleep(TICK_SECONDS)


# ---------------------------------------------------------------------------

def load_config():
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
    else:
        with open(CONFIG_PATH, "w") as f:
            json.dump({"bot_token": "", "admin_chat_id": ""}, f, indent=2)
        os.chmod(CONFIG_PATH, 0o600)
    if os.environ.get("TG_BOT_TOKEN"):
        cfg["bot_token"] = os.environ["TG_BOT_TOKEN"]
    if os.environ.get("ADMIN_CHAT_ID"):
        cfg["admin_chat_id"] = os.environ["ADMIN_CHAT_ID"]
    return cfg


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    cfg = load_config()
    token = cfg.get("bot_token")

    if cmd == "check":
        store.connect()
        sent, considered, fetched = watcher_pass(None, {}, dry_run=True)
        log(f"{considered} showtimes tracked, {fetched} seat maps read.")
        return

    if not token:
        sys.exit(f"No bot token. Put one in {CONFIG_PATH} — see HOW-TO-USE.md")
    tg = Telegram(token)

    if cmd == "whoami":
        me = tg.me()
        print(f"Connected as @{me.get('username')} ({me.get('first_name')})")
        return

    if cmd != "run":
        sys.exit("Usage: bot.py [run|check|whoami]")

    store.connect()
    me = tg.me()
    log(f"Bot @{me.get('username')} starting. Cap is {MAX_USERS} users.")
    threading.Thread(target=conversation_loop, args=(tg,), daemon=True).start()
    watcher_loop(tg, cfg.get("admin_chat_id"))


if __name__ == "__main__":
    main()
