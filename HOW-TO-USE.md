# How to use the Odyssey seat bot

You don't need to know anything about Telegram or bots. This walks through all of it.

**What it does:** *The Odyssey* in IMAX 70mm is sold out almost everywhere. Occasionally someone cancels and a seat frees up — often for only a few minutes. This watches both Bay Area theatres that show the film in 70mm and sends a message to your phone the moment a seat you'd actually want becomes available.

---

## Step 1 — Install Telegram

Telegram is a free messaging app, like WhatsApp.

- **iPhone:** App Store → search **Telegram** → Get
- **Android:** Play Store → search **Telegram** → Install

Open it and sign up with your phone number. It texts you a code to confirm. That's the whole signup — no email, no password.

> **Why Telegram and not a text message or email?** Texts cost money to send and email gets buried. Telegram is instant, free, and buzzes your phone like a message from a friend. Since seats can vanish in minutes, that speed is the entire point.

## Step 2 — Find the bot

A "bot" is just an automated account. It isn't a person and it isn't AI — think of it as an app that lives inside your chat list.

In Telegram, tap the **search icon** (magnifying glass, top of screen) and type:

```
@ODYSSEY_BOT_USERNAME
```

Tap it in the results. A chat opens.

## Step 3 — Tap START

At the bottom of the screen there's a big **START** button. Tap it.

> **Why this matters:** Telegram doesn't let a bot message you until you've messaged it first. It's an anti-spam rule. If you skip this, the bot physically cannot reach you — so don't close the chat before tapping START.

## Step 4 — Answer four questions

The bot asks four things. Every answer is a button — you never type anything.

**Which theatre?**

| | |
|---|---|
| **AMC Metreon** | San Francisco, downtown |
| **Regal Hacienda** | Dublin, about 30 miles east |
| **Both** | Alerts from either |

**How far back do you want to sit?**

Front rows are almost always available, because nobody wants to sit with their neck craned back. So the bot ignores them entirely — this question is about how much of the rest you'll accept.

| | |
|---|---|
| **Back rows only** | The last four rows. Fewest alerts, best seats. |
| **Back half** | The back half of the room. |
| **Middle & back** | Everything except the front few rows. Most alerts. |

Each option includes everything further back — if you choose "back half" and a back-row seat opens, you'll still hear about it.

**Where in the row?**

| | |
|---|---|
| **Center only** | Skips the five seats at each end of every row |
| **Edges are fine** | Any seat in the row |

**When could you actually go?**

| | |
|---|---|
| **Evenings & weekends** | Weeknights from 6pm, plus all day Saturday and Sunday |
| **Weekends only** | Saturday and Sunday |
| **Anytime** | Including weekday mornings and afternoons |

That's it. You're watching.

## Step 5 — Wait

**Silence is normal, and it may last days.** Good seats genuinely are rare — that's why this exists. If it pinged you constantly, the filter wouldn't be doing its job.

When something opens, you'll get:

```
🎟️ Metreon — 2 seats open
K18, K19  ·  center, back rows
Mon Aug 17, 6:00 PM
https://tickets.fandango.com/...
```

**Tap the link and book immediately.** The seat is not held for you — you're being told it exists, and anyone browsing the site can take it. Seats have been known to disappear within an hour, sometimes minutes.

> **Tip:** be logged into the theatre's site or app already, with a card saved. The difference between booking in 30 seconds and 3 minutes is often the difference between getting the seat and not.

---

## Commands

Type these into the chat any time.

| Command | What it does |
|---|---|
| `/status` | Shows what the bot is currently watching for you |
| `/pause` | Stops alerts without losing your settings |
| `/resume` | Starts them again |
| `/stop` | Leaves, and deletes your details completely |
| `/test` | Sends you a fake alert, to check notifications work |
| `/start` | Sign up, or change your answers |

**Run `/test` right after signing up.** If it doesn't buzz your phone, fix that now rather than discovering it when a real seat opens.

---

## Make sure alerts actually reach you

This is the one thing worth two minutes of your time. An alert you don't notice is the same as no alert.

**On iPhone:**
1. Open the bot chat, tap its name at the top, check **Notifications** are on
2. Settings → Notifications → Telegram → **Allow Notifications** on
3. If you use a **Focus** mode (Do Not Disturb, Sleep, Work), add Telegram as an allowed app — otherwise it'll be silenced exactly when you're not looking at your phone

**On Android:** Settings → Apps → Telegram → Notifications → on. If you use Do Not Disturb, allow Telegram through it.

---

## Common questions

**Does it book the ticket for me?**
No. It tells you a seat exists and gives you the link. You book it yourself. Nothing automated ever touches your payment details.

**Does it cost anything?**
No. Telegram is free and the bot is free.

**Will it spam me?**
No. It batches everything for one showing into a single message, never tells you about the same seat twice, and limits how many messages it sends in one go.

**What do you store about me?**
A Telegram chat ID and your four answers. No name, no phone number, no email — the bot can't see any of those. `/stop` deletes your record entirely.

**Why did my friend get an alert and I didn't?**
Different settings. If they chose "middle & back" and you chose "back rows only," they hear about far more seats. Check yours with `/status`.

**It's been days with nothing. Is it broken?**
Probably not — that's the normal state. Run `/test`; if the fake alert arrives, the bot is reaching you fine and there simply haven't been any good seats.

**Can I be more specific about what I want?**
Not through the bot. Four questions covers most people, and every extra option is another thing to get wrong during signup.

**What happens when the film's run ends?**
There'll be no showtimes left, so the alerts stop. You'll be told before anything is deleted.
