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
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import db

SCOPES = ["https://www.googleapis.com/auth/calendar.events",
          "https://www.googleapis.com/auth/tasks"]
GCP_DIR = os.path.expanduser("~/.listener-gcp")
CLIENT_SECRET = os.path.join(GCP_DIR, "client_secret.json")
TOKEN = os.path.join(GCP_DIR, "token.json")
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


def _svc(name, version, creds):
    from googleapiclient.discovery import build
    return build(name, version, credentials=creds, cache_discovery=False)


def create_event(creds, summary, due_utc_iso, description=""):
    start = datetime.fromisoformat(due_utc_iso).astimezone(TZ)
    end = start + timedelta(hours=EVENT_HOURS)
    body = {
        "summary": summary, "description": description,
        "start": {"dateTime": start.isoformat(), "timeZone": TZ_NAME},
        "end": {"dateTime": end.isoformat(), "timeZone": TZ_NAME},
        "reminders": {"useDefault": False,
                      "overrides": [{"method": "popup", "minutes": REMIND_MIN}]},
    }
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
    n = 0
    for it in db.unsynced_intents(conn):
        kind = (it["kind"] or "task").lower()
        desc = f'Listener · {it["who"]}: "{it["source_quote"] or ""}"'
        try:
            if kind == "event" and it["due_at"]:
                eid, link = create_event(creds, it["action"], it["due_at"], desc)
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
