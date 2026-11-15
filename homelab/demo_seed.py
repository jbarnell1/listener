#!/usr/bin/env python3
"""Fill an EMPTY Listener database with invented demo data.

Nothing here was ever recorded: the people, conversations and tasks are made up so the
dashboard can be tried, tested and screenshotted without a wearable, a GPU or anyone's
real speech. The script refuses to touch a database that already holds a transcript.

    LISTENER_DB=/tmp/listener-demo.db python demo_seed.py
    LISTENER_DB=/tmp/listener-demo.db uvicorn app:app --port 8000
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import db

NOW = datetime.now(timezone.utc).replace(microsecond=0)
LOCAL = NOW.astimezone(ZoneInfo(db.TZ_NAME))
# Weekday names the invented speakers use, so "the plumber comes Saturday" always matches
# the due date whatever day the demo is seeded on.
DAY = {f"d{n}": (LOCAL + timedelta(days=n)).strftime("%A") for n in range(1, 8)}
WEEKEND = (6 - LOCAL.weekday()) % 7          # days until Sunday


def _utc(minutes_ago=0, days_ahead=0, hour=None):
    """A UTC timestamp string in the format SQLite's datetime('now') produces."""
    dt = NOW - timedelta(minutes=minutes_ago) + timedelta(days=days_ahead)
    if hour is not None:
        dt = dt.replace(hour=hour, minute=0, second=0)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _due(days_ahead, local_hour):
    """Due time as the pipeline stores it: UTC ISO, from a local wall-clock hour."""
    local = (LOCAL + timedelta(days=days_ahead)).replace(hour=local_hour, minute=0, second=0)
    return local.astimezone(timezone.utc).isoformat()


PEOPLE = [                       # (name, relationship, is_self, status)
    ("Alex", None, 1, "enrolled"),
    ("Priya", "coworker", 0, "enrolled"),
    ("Jordan", "coworker", 0, "enrolled"),
    ("Sam", "partner", 0, "enrolled"),
    ("Riley", "friend", 0, "enrolled"),
    (None, None, 0, "unknown"),
]

CONVERSATIONS = [
    {"minutes_ago": 540, "tags": ["standup", "release"], "lines": [
        ("Priya", "Morning. Quick standup, then I have to run to the vendor call."),
        ("Alex", "Sure. Yesterday I finished the export screen, today I'm on the sync bug."),
        ("Jordan", "I'm still blocked on the staging database. Can someone reset my access?"),
        ("Alex", "I'll reset your staging access right after this."),
        ("Priya", "Thanks. The release is still {d3}, so the changelog needs to be done "
                  "by {d2} night."),
        ("Alex", "I'll draft the changelog tomorrow morning and send it round."),
        ("Jordan", "Once I'm unblocked I can take the flaky login test."),
        ("Priya", "Great. Let's do a release dry run {d2} at two."),
    ]},
    {"minutes_ago": 300, "tags": ["groceries", "home"], "lines": [
        ("Sam", "We're out of coffee again, and the oat milk is nearly gone."),
        ("Alex", "I can stop at the shop on the way home. What else?"),
        ("Sam", "Eggs, spinach, a lemon, and something for {d1}. Maybe tacos?"),
        ("Alex", "Tacos it is. Tortillas, black beans, an avocado."),
        ("Sam", "And dish soap. Oh, and the plumber is coming {d4} at ten for the "
                "kitchen tap."),
        ("Alex", "Got it. I'll be home for that."),
    ]},
    {"minutes_ago": 150, "tags": ["podcast", "ideas"], "lines": [
        ("Riley", "Okay, podcast idea. Ten minute episodes, one odd tool per episode."),
        ("Alex", "Like the label maker, or the pocket microscope?"),
        ("Riley", "Exactly. We each bring one, the other one has to guess what it is for."),
        ("Alex", "I like it. Working title: Small Tools."),
        ("Riley", "I can borrow two microphones from the library studio next week."),
        ("Alex", "I'll sketch three episode outlines this weekend and we can pick one."),
        ("Riley", "Deal. Let's record a pilot a week from tomorrow."),
    ]},
    {"minutes_ago": 35, "tags": ["home"], "lines": [
        ("Alex", "Remind me to renew the library card before the end of the month."),
        (None, "Excuse me, is this seat taken?"),
        ("Alex", "No, go ahead."),
    ]},
]

TASKS = [   # (conversation, speaker, action, tier, kind, days_ahead, local_hour, quote, conf, extra)
    (0, "Alex", "Reset Jordan's staging access", "SOON", "task", 0, 17,
     "I'll reset your staging access right after this.", 0.93, {"gtask_id": "demo"}),
    (0, "Alex", "Draft the release changelog", "SOON", "task", 1, 9,
     "I'll draft the changelog tomorrow morning and send it round.", 0.91, {"gtask_id": "demo"}),
    (0, "Priya", "Release dry run", "SOON", "event", 2, 14,
     "Let's do a release dry run {d2} at two.", 0.88,
     {"calendar_event_id": "demo", "calendar_link": "#"}),
    (1, "Alex", "Buy coffee, oat milk, eggs, spinach, a lemon, taco things, dish soap",
     "SOON", "task", 0, 18, "I can stop at the shop on the way home.", 0.9, {"gtask_id": "demo"}),
    (1, "Sam", "Plumber visit for the kitchen tap", "SOON", "event", 4, 10,
     "the plumber is coming {d4} at ten for the kitchen tap", 0.86,
     {"calendar_event_id": "demo", "calendar_link": "#"}),
    (2, "Alex", "Sketch three episode outlines for Small Tools", "LATER", "task", WEEKEND, 16,
     "I'll sketch three episode outlines this weekend and we can pick one.", 0.84, {}),
    (2, "Riley", "Record the podcast pilot", "LATER", "event", 8, 19,
     "Let's record a pilot a week from tomorrow.", 0.8, {}),
    (3, "Alex", "Renew the library card", "LATER", "followup", None, None,
     "Remind me to renew the library card before the end of the month.", 0.95, {}),
]

SUGGESTED = [   # held in the Review queue (low confidence)
    (0, "Jordan", "Take over the flaky login test", "LATER", "task",
     "Once I'm unblocked I can take the flaky login test.", 0.58, "Jordan"),
    (2, "Riley", "Borrow two microphones from the library studio", "LATER", "task",
     "I can borrow two microphones from the library studio next week.", 0.55, "Riley"),
]

PROFILES = {
    "Priya": dict(
        summary="Engineering lead on Alex's team. Runs the morning standup and owns the "
                "release schedule.",
        recent="Focused on this week's release; short on time between vendor calls.",
        traits=["direct", "organised", "protects the team's focus"],
        interests=["trail running", "sourdough"],
        dislikes=["meetings without an agenda"],
        dates=[{"label": "Work anniversary", "date": "March 3"}],
        notable=["Leads the weekly release", "Prefers written updates"]),
    "Sam": dict(
        summary="Alex's partner. Keeps the household list and plans the weekend meals.",
        recent="Planning taco night; waiting on the plumber for the kitchen tap.",
        traits=["practical", "plans ahead"],
        interests=["cooking", "houseplants", "board games"],
        dislikes=["running out of coffee"],
        dates=[{"label": "Birthday", "date": "June 14"}],
        notable=["Drinks oat milk", "Booked the plumber for the kitchen tap"]),
    "Riley": dict(
        summary="Old friend and podcast co-host. Full of side project ideas.",
        recent="Excited about the Small Tools podcast pilot.",
        traits=["enthusiastic", "curious"],
        interests=["odd gadgets", "audio gear", "cycling"],
        dislikes=[],
        dates=[],
        notable=["Can borrow microphones from the library studio"]),
}

TAG_SUMMARIES = {
    "standup": "Daily team standup. Alex is on the sync bug, Jordan was blocked on staging "
               "access, Priya is steering toward the {d3} release.",
    "release": "Release is {d3}. Changelog due {d2} night, dry run {d2} at two.",
    "groceries": "Running list: coffee, oat milk, eggs, spinach, a lemon, taco ingredients, "
                 "dish soap.",
    "home": "Household logistics: the plumber comes {d4} at ten; library card renewal "
            "is due this month.",
    "podcast": "Small Tools: ten minute episodes about one odd tool each. Pilot recording "
               "planned for a week from tomorrow.",
    "ideas": "Side project ideas worth keeping. Currently the Small Tools podcast.",
}

REFLECTION = [
    "The {d3} release shows up in three separate conversations; the changelog is the one "
    "piece nobody has started.",
    "Coffee has run out twice this month. A standing weekly order might be simpler.",
    "The podcast idea has a title, a format and a pilot date, but no outlines yet.",
]


def seed(conn):
    if conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0]:
        raise SystemExit("demo_seed: this database already has transcripts; refusing to "
                         "write demo data into it. Point LISTENER_DB at a new file.")
    cur = conn.cursor()
    sid = {}
    for name, rel, is_self, status in PEOPLE:
        cur.execute("INSERT INTO speakers(name, relationship, status, is_self, created_at) "
                    "VALUES(?,?,?,?,?)", (name, rel, status, is_self, _utc(60 * 24 * 20)))
        sid[name] = cur.lastrowid

    tid, first_seg = [], {}
    for n, convo in enumerate(CONVERSATIONS):
        when = _utc(convo["minutes_ago"])
        cur.execute("INSERT INTO chunks(device, seq, ts_start, codec, bytes, path, acked, "
                    "transcribed, created_at) VALUES('listener-01',?,?,?,?,?,1,1,?)",
                    (n + 1, when, "adpcm", 96000 + 8000 * n, "", when))
        cur.execute("INSERT INTO transcripts(chunk_id, audio_path, lang, created_at) "
                    "VALUES(?,?,?,?)", (cur.lastrowid, "", "en", when))
        t = cur.lastrowid
        tid.append(t)
        clock = 0.4
        for who, text in convo["lines"]:
            text = text.format(**DAY)
            length = 1.2 + len(text) / 16.0
            cur.execute("INSERT INTO segments(transcript_id, speaker_id, t_start, t_end, text) "
                        "VALUES(?,?,?,?,?)", (t, sid[who], round(clock, 1),
                                              round(clock + length, 1), text))
            first_seg.setdefault((n, who), cur.lastrowid)
            clock += length + 0.6
        for tag in convo["tags"]:
            db.tag_transcript(conn, t, db.get_or_create_tag(conn, tag))

    for (c, who, action, tier, kind, days, hour, quote, conf, extra) in TASKS:
        cols = {"segment_id": first_seg.get((c, who)), "transcript_id": tid[c],
                "speaker_id": sid[who], "action": action, "tier": tier, "kind": kind,
                "due_at": _due(days, hour) if days is not None else None, "owner": "me",
                "status": "pending", "source_quote": quote.format(**DAY), "confidence": conf,
                "importance": 3, "created_at": _utc(CONVERSATIONS[c]["minutes_ago"] - 1),
                "synced_at": _utc(CONVERSATIONS[c]["minutes_ago"] - 1), **extra}
        cur.execute(f"INSERT INTO intents({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                    list(cols.values()))
    for (c, who, action, tier, kind, quote, conf, owner) in SUGGESTED:
        cur.execute("INSERT INTO intents(segment_id, transcript_id, speaker_id, action, tier, "
                    "kind, owner, status, source_quote, confidence, importance, created_at) "
                    "VALUES(?,?,?,?,?,?,?,'suggested',?,?,2,?)",
                    (first_seg.get((c, who)), tid[c], sid[who], action, tier, kind, owner,
                     quote, conf, _utc(CONVERSATIONS[c]["minutes_ago"] - 1)))
    # one item the reconciler closed after hearing it was done (shows the undo row)
    cur.execute("INSERT INTO intents(transcript_id, speaker_id, action, tier, kind, owner, "
                "status, source_quote, confidence, close_kind, close_note, closed_at, created_at) "
                "VALUES(?,?,?,?,?,?,'dismissed',?,?,?,?,?,?)",
                (tid[0], sid["Alex"], "Finish the export screen", "SOON", "task", "me",
                 "I'll have the export screen done by Monday.", 0.9, "completed",
                 "Yesterday I finished the export screen", _utc(538), _utc(60 * 30)))
    conn.commit()

    for name, p in PROFILES.items():
        db.upsert_profile(conn, sid[name], last_seen=_utc(120), **p)
    for tag, summary in TAG_SUMMARIES.items():
        db.set_tag_summary(conn, db.get_or_create_tag(conn, tag), summary.format(**DAY))

    db.upsert_device_status(conn, {
        "device": "listener-01", "battery_mv": 3985, "rssi": -57, "ssid": "home-wifi",
        "ip": "192.0.2.42", "uptime_s": 6 * 3600 + 41 * 60, "free_heap": 6200000,
        "fw": "v0.6-speechdetect", "seq": 418})
    db.create_device_key(conn, "listener-01", "Demo board")
    db.meta_set(conn, "consent_accepted_at", _utc(60 * 24 * 19))
    db.meta_set(conn, "reflection", json.dumps([r.format(**DAY) for r in REFLECTION]))
    db.meta_set(conn, "reflection_at", NOW.isoformat())
    db.meta_set(conn, "core_memory", "- I work from home on Tuesdays and Thursdays\n"
                                     "- My partner is Sam\n- 'the shop' = the corner grocery")
    db.meta_set(conn, "activity_seen_at", _utc(200))
    conn.commit()
    return {"speakers": len(PEOPLE), "transcripts": len(tid),
            "tasks": len(TASKS) + len(SUGGESTED) + 1}


if __name__ == "__main__":
    summary = seed(db.init_db())
    print(f"demo data written to {db.DB_PATH}: {summary}", file=sys.stderr)
