#!/usr/bin/env python3
"""Push extracted intents to Google Calendar / Tasks (ADR-026).

Semantic routing (set by the LLM's `kind`):
    event    → Google Calendar event (exact time + popup reminder)
    task     → Google Task (due DATE; the Tasks API discards time-of-day)
    followup → nothing here — stays for the nightly email digest

Auth is OAuth 2.0 (NOT the Gmail app password — that's SMTP-only). One-time:
    1) GCP project → enable Calendar API + Tasks API
    2) OAuth consent screen → External, **Published** (Testing tokens die in 7 days)
    3) Create OAuth client (Desktop) → download JSON to ~/.listener-gcp/client_secret.json
    4) python google_sync.py --auth      # opens a URL; authorize in your browser
Then:
    python google_sync.py --status        # connected?
    python google_sync.py --sync          # push any pending intents now
"""
import os
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import db


def _rrule(rec):
    """Map our recurrence shorthand to a Google Calendar RRULE (ADR-038).
    daily | weekly:TU | biweekly:FR | monthly  ->  RRULE:FREQ=...;[INTERVAL=2;][BYDAY=..]"""
    if not rec:
        return None
    r = rec.strip().lower()
    if r == "daily":
        return "RRULE:FREQ=DAILY"
    if r == "monthly":
        return "RRULE:FREQ=MONTHLY"
    m = re.match(r"(weekly|biweekly)(?::([a-z]{2}))?$", r)
    if m:
        interval = ";INTERVAL=2" if m.group(1) == "biweekly" else ""
        day = (m.group(2) or "").upper()
        byday = f";BYDAY={day}" if day in {"MO", "TU", "WE", "TH", "FR", "SA", "SU"} else ""
        return f"RRULE:FREQ=WEEKLY{interval}{byday}"
    return None

SCOPES = ["https://www.googleapis.com/auth/calendar.events",
          "https://www.googleapis.com/auth/tasks"]
GCP_DIR = os.path.expanduser("~/.listener-gcp")
CLIENT_SECRET = os.path.join(GCP_DIR, "client_secret.json")          # desktop (CLI loopback)
WEB_CLIENT = os.path.join(GCP_DIR, "client_secret_web.json")         # web (UI flow, ADR-044)
TOKEN = os.path.join(GCP_DIR, "token.json")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")             # Google adds 'openid'
TZ_NAME = "America/Chicago"
TZ = ZoneInfo(TZ_NAME)
EVENT_HOURS = 1            # default event duration
REMIND_MIN = 10           # popup reminder before an event


def _save(creds):
    os.makedirs(GCP_DIR, exist_ok=True)
    with open(TOKEN, "w") as f:
        f.write(creds.to_json())
    os.chmod(TOKEN, 0o600)


def get_credentials():
    """Loaded + refreshed OAuth creds, or None if not connected/authorized."""
    if not os.path.exists(TOKEN):
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save(creds)
        except Exception as e:  # noqa: BLE001
            print(f"google: token refresh failed: {e}")
            return None
    return creds if creds and creds.valid else None


def configured():
    return get_credentials() is not None


def sync_enabled(conn=None):
    """The shutoff valve (ADR-034). When OFF, items still reach the email digest and
    dashboard, but nothing is pushed to — or removed from — Google Calendar/Tasks."""
    return db.meta_get(conn or db.connect(), "google_sync_enabled", "1") != "0"


def status():
    return {"connected": configured(),
            "client_secret": os.path.exists(CLIENT_SECRET), "token": os.path.exists(TOKEN)}


def authorize():
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not os.path.exists(CLIENT_SECRET):
        raise SystemExit(f"missing {CLIENT_SECRET}\n"
                         "Download your OAuth *Desktop* client JSON there first.")
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
    creds = flow.run_local_server(
        port=0, open_browser=False,
        authorization_prompt_message="\nAuthorize Listener — open this URL in your browser:\n\n{url}\n",
        success_message="Authorized. You can close this tab and return to the terminal.")
    _save(creds)
    print(f"google: authorized; token saved → {TOKEN}")


def _web_client_file():
    """Prefer a dedicated Web OAuth client for the browser flow; fall back to the main one."""
    return WEB_CLIENT if os.path.exists(WEB_CLIENT) else CLIENT_SECRET


def web_auth_url(redirect_uri):
    """Build Google's consent URL for the in-dashboard (browser) re-auth flow (ADR-044)."""
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_secrets_file(_web_client_file(), scopes=SCOPES, redirect_uri=redirect_uri)
    url, state = flow.authorization_url(access_type="offline",
                                        include_granted_scopes="true", prompt="consent")
    return url, state


def web_finish(redirect_uri, code):
    """Exchange the callback `code` for tokens and persist them (browser flow)."""
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_secrets_file(_web_client_file(), scopes=SCOPES, redirect_uri=redirect_uri)
    flow.fetch_token(code=code)
    _save(flow.credentials)


DASHBOARD_DEFAULT = "https://jon-desktop.taildc59f0.ts.net"   # tailnet dashboard (link target)


def _describe(conn, it):
    """Body for the Google task/event: the triggering snippet (toggleable), the Listener
    attribution, and a backlink to the source conversation on the dashboard (ADR-052)."""
    lines = []
    quote = (it["source_quote"] or "").strip()
    # Only owner==me items reach Google, but the quote can still contain another person's
    # words (e.g. "can you grab milk?"), so it's behind a toggle that relaxes ADR-042.
    if quote and db.meta_get(conn, "google_include_quote", "1") != "0":
        if len(quote) > 300:
            quote = quote[:297] + "…"
        lines.append(f'“{quote}”')
    lines.append(f'— via Listener · {it["who"]}')
    if it["transcript_id"]:
        from urllib.parse import quote
        base = db.meta_get(conn, "dashboard_url", DASHBOARD_DEFAULT).rstrip("/")
        tid = it["transcript_id"]
        lines.append(f'🗒 Conversation: {base}/transcripts/{tid}')
        ask = (f'Catch me up on transcript #{tid}: what was discussed, and why did it '
               f'create "{it["action"]}"?')
        lines.append(f'💬 Ask Listener: {base}/transcripts/{tid}?ask={quote(ask)}')
    return "\n".join(lines)


def _svc(name, version, creds):
    from googleapiclient.discovery import build
    return build(name, version, credentials=creds, cache_discovery=False)


def create_event(creds, summary, due_utc_iso, description="", duration_min=None,
                 remind_min=None, recurrence=None):
    duration_min = EVENT_HOURS * 60 if duration_min is None else duration_min
    remind_min = REMIND_MIN if remind_min is None else remind_min
    start = datetime.fromisoformat(due_utc_iso).astimezone(TZ)
    end = start + timedelta(minutes=duration_min)
    body = {
        "summary": summary, "description": description,
        "start": {"dateTime": start.isoformat(), "timeZone": TZ_NAME},
        "end": {"dateTime": end.isoformat(), "timeZone": TZ_NAME},
        "reminders": {"useDefault": False,
                      "overrides": [{"method": "popup", "minutes": remind_min}]},
    }
    rrule = _rrule(recurrence)
    if rrule:
        body["recurrence"] = [rrule]
    ev = _svc("calendar", "v3", creds).events().insert(calendarId="primary", body=body).execute()
    return ev["id"], ev.get("htmlLink")


def delete_event(creds, event_id):
    _svc("calendar", "v3", creds).events().delete(
        calendarId="primary", eventId=event_id).execute()


def delete_task(creds, task_id):
    _svc("tasks", "v1", creds).tasks().delete(tasklist="@default", task=task_id).execute()


def remove_intent(conn, intent_id):
    """Delete the Google item(s) backing an intent (called when the user dismisses
    it). Safe no-op if not connected, valve closed, or already gone."""
    if not sync_enabled(conn):
        return False
    creds = get_credentials()
    if not creds:
        return False
    r = conn.execute("SELECT calendar_event_id, gtask_id FROM intents WHERE id=?",
                     (intent_id,)).fetchone()
    if not r:
        return False
    try:
        if r["calendar_event_id"]:
            delete_event(creds, r["calendar_event_id"])
        if r["gtask_id"]:
            delete_task(creds, r["gtask_id"])
        return True
    except Exception as e:  # noqa: BLE001
        print(f"google: delete failed for intent {intent_id}: {e}")
        return False


def create_task(creds, title, due_utc_iso=None, notes=""):
    body = {"title": title}
    if notes:
        body["notes"] = notes
    if due_utc_iso:                       # Tasks API keeps DATE only (time discarded)
        d = datetime.fromisoformat(due_utc_iso).astimezone(TZ).date()
        body["due"] = f"{d.isoformat()}T00:00:00.000Z"
    return _svc("tasks", "v1", creds).tasks().insert(
        tasklist="@default", body=body).execute()["id"]


def sync_pending(conn, verbose=False):
    """Push every unsynced intent to its Google surface by kind. No-op (safe) if
    not connected or the valve is closed. Followups are marked synced (they belong
    to the email digest)."""
    if not sync_enabled(conn):
        if verbose:
            print("google: sync paused (shutoff valve) — items stay in email + dashboard")
        return {"connected": configured(), "synced": 0, "paused": True}
    creds = get_credentials()
    if not creds:
        if verbose:
            print("google: not connected — run `python google_sync.py --auth`")
        return {"connected": False, "synced": 0}
    dur = db.cfg(conn, "event_duration_min", EVENT_HOURS * 60)
    rem = db.cfg(conn, "event_reminder_min", REMIND_MIN)
    n = 0
    for it in db.unsynced_intents(conn):
        # Privacy (ADR-042): only the wearer's own items go to Google. Someone else's
        # item stays local (digest + dashboard), and we never send verbatim third-party
        # speech (the source quote) off the box.
        owner = (it["owner"] or "me").strip().lower()
        if owner not in ("me", "myself", "i", ""):
            db.mark_intent_synced(conn, it["id"])      # local-only; not pushed to Google
            n += 1
            if verbose:
                print(f"  kept local (owner={owner}) {it['action']}")
            continue
        kind = (it["kind"] or "task").lower()
        rec = it["recurrence"]
        desc = _describe(conn, it)
        try:
            # A recurring item with a time is most useful as a recurring Calendar event,
            # even if the model called it a "task" (Google Tasks can't recur). ADR-038.
            if (kind == "event" or rec) and it["due_at"]:
                eid, link = create_event(creds, it["action"], it["due_at"], desc,
                                         duration_min=dur, remind_min=rem, recurrence=rec)
                db.mark_intent_synced(conn, it["id"], calendar_event_id=eid, calendar_link=link)
            elif kind == "followup":
                db.mark_intent_synced(conn, it["id"])          # digest-only
                continue
            else:                                              # task (or timeless event)
                db.mark_intent_synced(conn, it["id"],
                                      gtask_id=create_task(creds, it["action"],
                                                           it["due_at"], desc))
            n += 1
            if verbose:
                print(f"  synced [{kind}] {it['action']}")
        except Exception as e:  # noqa: BLE001
            print(f"  google sync failed for intent {it['id']}: {e}")
    return {"connected": True, "synced": n}


if __name__ == "__main__":
    if "--auth" in sys.argv:
        authorize()
    elif "--status" in sys.argv:
        print(status())
    elif "--sync" in sys.argv:
        print(sync_pending(db.connect(), verbose=True))
    else:
        print(__doc__)
