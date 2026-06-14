#!/usr/bin/env python3
"""H5 — email delivery: a nightly brief over Gmail SMTP.  ADR-024.

Fully local SMTP client (no third-party service). Credentials come from the
environment — loaded from ~/.listener.env by listener.sh, NEVER committed:
    LISTENER_SMTP_USER   your gmail address
    LISTENER_SMTP_PASS   a Google App Password (16 chars; NOT your login password)
    LISTENER_MAIL_TO     recipient (defaults to LISTENER_SMTP_USER)

The brief is sent at 23:50 local (scheduled in app.py) so it lands before midnight
and is captured by the next-morning Google Daily Brief.

CLI:
    python mailer.py --brief          # print tonight's brief (no send)
    python mailer.py --brief --send   # compose + send it
    python mailer.py --test           # send a one-line deliverability test
"""
import os
import smtplib
import ssl
import sys
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from zoneinfo import ZoneInfo

import db
import reflect

TZ = ZoneInfo("America/Chicago")
UTC = ZoneInfo("UTC")
SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465


def _creds():
    user = os.environ.get("LISTENER_SMTP_USER")
    pwd = (os.environ.get("LISTENER_SMTP_PASS") or "").replace(" ", "")  # app pw often shown spaced
    to = os.environ.get("LISTENER_MAIL_TO") or user
    return user, pwd, to


def configured() -> bool:
    user, pwd, _ = _creds()
    return bool(user and pwd)


def send(subject: str, text: str, html: str | None = None) -> bool:
    user, pwd, to = _creds()
    if not (user and pwd):
        print("mailer: no credentials set (LISTENER_SMTP_USER/PASS) — skipping send")
        return False
    msg = EmailMessage()
    msg["From"] = formataddr(("Listener", user))   # display name; address is still the Gmail acct
    msg["To"], msg["Subject"] = to, subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(),
                          timeout=30) as s:
        s.login(user, pwd)
        s.send_message(msg)
    print(f"mailer: sent '{subject}' to {to}")
    return True


def _local(iso: str) -> str:
    if not iso:
        return "no time"
    try:
        dt = datetime.fromisoformat(iso)
        dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
        return dt.astimezone(TZ).strftime("%a %-I:%M %p")
    except ValueError:
        return iso


def compose_brief(conn=None):
    """Digest only (ADR-026): timed reminders now live on Google Calendar/Tasks.
    Dated items appear as a heads-up; undated follow-ups are the email's real job."""
    conn = conn or db.connect()
    rows = db.list_intents(conn)
    dated = [r for r in rows if r["due_at"]]
    undated = [r for r in rows if not r["due_at"]]
    day = datetime.now(TZ).strftime("%A, %B %-d")
    insights = reflect.latest(conn)

    lines = [f"Listener — your brief for {day}", ""]
    if insights:
        lines.append("Patterns:")
        lines += [f"  • {s}" for s in insights]
        lines.append("")
    if dated:
        lines.append("Coming up (also on your Calendar / Tasks):")
        lines += [f"  - {t['action']}  ({t['who']} · {_local(t['due_at'])})" for t in dated]
        lines.append("")
    if undated:
        lines.append("Follow-ups (no deadline):")
        lines += [f"  - {t['action']}  ({t['who']})" for t in undated]
        lines.append("")
    if not dated and not undated:
        lines.append("Nothing on the list right now. Enjoy the quiet.")
    lines.append("\n— Listener (local, private)")

    def li(t, due=True):
        d = f" <span style='color:#7c9cff'>{_local(t['due_at'])}</span>" if due else ""
        return (f"<li style='margin:6px 0'>{t['action']} "
                f"<span style='color:#8a96a8'>· {t['who']}{d}</span></li>")
    html = ["<div style='font-family:system-ui,Segoe UI,sans-serif;max-width:560px'>",
            "<h2 style='margin:0 0 2px'>Your brief</h2>",
            f"<div style='color:#8a96a8;margin-bottom:14px'>{day}</div>"]
    if insights:
        html.append("<h3 style='margin:14px 0 4px'>Patterns</h3><ul style='padding-left:18px'>")
        html += [f"<li style='margin:6px 0'>{s}</li>" for s in insights]
        html.append("</ul>")
    if dated:
        html.append("<h3 style='margin:14px 0 2px'>Coming up</h3>"
                    "<div style='color:#8a96a8;font-size:12px;margin-bottom:4px'>"
                    "also on your Calendar &amp; Tasks</div><ul style='padding-left:18px'>")
        html += [li(t) for t in dated]; html.append("</ul>")
    if undated:
        html.append("<h3 style='margin:14px 0 4px'>Follow-ups</h3><ul style='padding-left:18px'>")
        html += [li(t, due=False) for t in undated]; html.append("</ul>")
    if not dated and not undated:
        html.append("<p>Nothing on the list right now. Enjoy the quiet.</p>")
    html.append("<p style='color:#8a96a8;font-size:12px;margin-top:18px'>"
                "— Listener · local &amp; private</p></div>")
    return f"Listener brief — {day}", "\n".join(lines), "".join(html)


def send_daily_brief() -> bool:
    subject, text, html = compose_brief()
    return send(subject, text, html)


if __name__ == "__main__":
    if "--test" in sys.argv:
        ok = send("Listener test ✅", "If you're reading this, email delivery works.")
        sys.exit(0 if ok else 1)
    if "--brief" in sys.argv:
        subj, text, _ = compose_brief()
        print(f"Subject: {subj}\n\n{text}")
        if "--send" in sys.argv:
            send_daily_brief()
        sys.exit(0)
    print(__doc__)
