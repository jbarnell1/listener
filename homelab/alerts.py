#!/usr/bin/env python3
"""Device health alerts: battery low / charged, and "we haven't heard from it" (ADR-046).

Delivery is push-first, email-fallback: if web push (push.py) is wired and has
subscribers it goes to the phone; otherwise it falls back to the existing SMTP mailer.
Each condition fires ONCE on the crossing (state stored in meta) so you don't get spammed.
"""
from datetime import datetime, timezone

import db
import mailer

LOW_PCT     = 25     # alert at/below this state-of-charge
CHARGED_PCT = 90     # alert at/above this (so you can unplug)
RESET_LOW   = 35     # battery must recover past here before "low" can fire again
RESET_HIGH  = 80     # must drop below here before "charged" can fire again
STALE_MIN   = 30     # no telemetry for this many minutes -> "offline" nudge


def _notify(subject, text):
    """Prefer push to the phone; fall back to email. Never raises."""
    try:
        import push
        if push.broadcast(subject, text):
            return
    except Exception:  # noqa: BLE001 — push optional/not-yet-configured
        pass
    try:
        mailer.send(subject, text)
    except Exception as e:  # noqa: BLE001
        print(f"alerts: notify failed: {e}")


def on_telemetry(conn, data):
    """Called from /telemetry. Clears the stale flag (we just heard from it) and
    fires battery crossings."""
    dev = str(data.get("device", "device"))
    db.meta_set(conn, f"alert_stale_{dev}", "0")          # alive again
    mv = data.get("battery_mv")
    pct = db.lipo_pct(mv) if mv else None
    if pct is None:
        return
    state = db.meta_get(conn, f"alert_batt_{dev}", "ok")
    if pct <= LOW_PCT and state != "low":
        db.meta_set(conn, f"alert_batt_{dev}", "low")
        _notify(f"🔋 Listener battery low — {pct}%",
                f"{dev} is at {pct}% ({mv} mV). Time to put it on the charger.")
    elif pct >= CHARGED_PCT and state != "charged":
        db.meta_set(conn, f"alert_batt_{dev}", "charged")
        _notify(f"🔌 Listener charged — {pct}%",
                f"{dev} reached {pct}% ({mv} mV). You can unplug it.")
    elif RESET_LOW < pct < RESET_HIGH and state != "ok":
        db.meta_set(conn, f"alert_batt_{dev}", "ok")       # back in mid-band -> re-arm both


def check_stale(conn=None):
    """Scheduler job: nudge once if a device has gone quiet (needs charge or a network)."""
    conn = conn or db.connect()
    now = datetime.now(timezone.utc)
    for r in db.device_status_list(conn):
        dev = r["device_id"]
        last = r["updated_at"]
        if not last:
            continue
        try:
            seen = datetime.strptime(last, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        age_min = (now - seen).total_seconds() / 60.0
        flagged = db.meta_get(conn, f"alert_stale_{dev}", "0") == "1"
        if age_min >= STALE_MIN and not flagged:
            db.meta_set(conn, f"alert_stale_{dev}", "1")
            _notify("📡 Listener went quiet",
                    f"No word from {dev} in {int(age_min)} min — it may need charging or a "
                    f"network. If you're out, turn on your phone hotspot so it can drain its "
                    f"buffer.")
