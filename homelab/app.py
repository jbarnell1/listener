#!/usr/bin/env python3
"""Listener dashboard + ingest (FastAPI + HTMX).  ADR-019.

Runs in ~/listener-web. Tailnet-only dashboard (Tailscale Serve); only /ingest is
exposed publicly (Tailscale Funnel), HMAC + replay-window locked.

    cd /mnt/c/Listener/homelab
    ~/listener-web/bin/uvicorn app:app --host 0.0.0.0 --port 8000 --reload
"""
import contextlib
import hashlib
import hmac
import html
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import assistant
import db
import google_sync
import gpu_gate
import mailer
import purge
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

HERE = os.path.dirname(os.path.abspath(__file__))
CHUNK_DIR = os.path.join(HERE, "data", "chunks")
os.makedirs(CHUNK_DIR, exist_ok=True)
INGEST_SECRET = os.environ.get("LISTENER_INGEST_SECRET", "dev-secret-change-me")

def _wait_gone(pattern, tries=60):
    """Block until no process matches `pattern` (so a restart doesn't race the old
    process for its port). ~6s max."""
    for _ in range(tries):
        if subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode != 0:
            return
        time.sleep(0.1)


class MCPManager:
    """Owns the dedicated MCP server subprocess (ADR-020). Singleton via pkill."""

    def __init__(self):
        self.proc = None

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        subprocess.run(["pkill", "-f", "[m]cp_server.py"])
        _wait_gone("[m]cp_server.py")          # let port 8765 free before rebinding
        logf = open(os.path.join("/tmp", "listener-mcp.log"), "a")  # visible on crash
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(HERE, "mcp_server.py")], cwd=HERE,
            stdout=logf, stderr=logf)

    def stop(self):
        subprocess.run(["pkill", "-f", "[m]cp_server.py"])
        self.proc = None


class WorkerManager:
    """Owns the pipeline worker subprocess (ADR-025). Singleton via pkill."""

    def __init__(self):
        self.proc = None

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        subprocess.run(["pkill", "-f", "[w]orker.py"])
        _wait_gone("[w]orker.py")              # avoid two workers grabbing one chunk
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(HERE, "worker.py")], cwd=HERE)

    def stop(self):
        subprocess.run(["pkill", "-f", "[w]orker.py"])
        self.proc = None


mcp_mgr = MCPManager()
worker_mgr = WorkerManager()
scheduler = AsyncIOScheduler(timezone=ZoneInfo("America/Chicago"))
BRIEF_HOUR, BRIEF_MIN = 23, 50   # 11:50 PM local — lands before midnight (ADR-024)


def _worker_status():
    try:
        with open("/tmp/listener-worker.json") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


@contextlib.asynccontextmanager
async def lifespan(_app):
    db.init_db()          # ensure schema + run idempotent migrations
    mcp_mgr.start()       # MCP server for the assistant
    worker_mgr.start()    # pipeline worker draining the ingest queue
    scheduler.add_job(mailer.send_daily_brief,
                      CronTrigger(hour=BRIEF_HOUR, minute=BRIEF_MIN),
                      id="daily_brief", replace_existing=True, misfire_grace_time=3600)
    scheduler.add_job(purge.purge_old_audio, CronTrigger(hour=3, minute=0),
                      id="audio_purge", replace_existing=True, misfire_grace_time=7200)
    scheduler.start()     # nightly email brief + 3 AM audio purge (ADR-021)
    yield
    scheduler.shutdown(wait=False)
    worker_mgr.stop()
    mcp_mgr.stop()


app = FastAPI(title="Listener", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
tpl = Jinja2Templates(directory=os.path.join(HERE, "templates"))


def _initials(label):
    if not label or str(label).lower().startswith("unknown"):
        return "?"
    parts = [p for p in str(label).replace("_", " ").split() if p]
    return (parts[0][:1] + (parts[1][:1] if len(parts) > 1 else "")).upper()


def _hue(sid):
    try:
        return (int(sid) * 53 + 17) % 360
    except (TypeError, ValueError):
        return 212


_CT = ZoneInfo("America/Chicago")
_UTC = ZoneInfo("UTC")


def _localtime(iso):
    if not iso:
        return "no time"
    try:
        dt = datetime.fromisoformat(iso)
        dt = dt.replace(tzinfo=_UTC) if dt.tzinfo is None else dt
        return dt.astimezone(_CT).strftime("%a %b %-d · %-I:%M %p")
    except ValueError:
        return iso


tpl.env.filters["initials"] = _initials
tpl.env.filters["hue"] = _hue
tpl.env.filters["localtime"] = _localtime


def page(name, request, **ctx):
    if "new_count" not in ctx:                # activity badge on every page
        try:
            c = db.connect()
            ctx["new_count"] = db.activity_count(
                c, db.meta_get(c, "activity_seen_at", "1970-01-01"))
        except Exception:  # noqa: BLE001
            ctx["new_count"] = 0
    return tpl.TemplateResponse(request, name, ctx)


def _hx(request):
    return request.headers.get("HX-Request") == "true"


# ---- dashboard (tailnet-only) ----
def _task_groups(rows):
    """Group open intents into ordered day-buckets for the dashboard."""
    today = datetime.now(_CT).date()
    order = ["Overdue", "Today", "Tomorrow", "This week", "Later", "Someday"]
    b = {k: [] for k in order}
    for r in rows:
        if not r["due_at"]:
            b["Someday"].append(r)
            continue
        try:
            d = datetime.fromisoformat(r["due_at"]).astimezone(_CT).date()
        except ValueError:
            b["Someday"].append(r)
            continue
        if d < today:
            b["Overdue"].append(r)
        elif d == today:
            b["Today"].append(r)
        elif d == today + timedelta(days=1):
            b["Tomorrow"].append(r)
        elif d <= today + timedelta(days=7):
            b["This week"].append(r)
        else:
            b["Later"].append(r)
    return [(k, b[k]) for k in order if b[k]]


def _review_nudges(c):
    """Small, actionable 'needs review' items for the dashboard."""
    nudges = []
    selfsp = db.get_self(c)
    enrolled = [s for s in db.list_speakers(c) if s["status"] == "enrolled"]
    if not selfsp and enrolled:
        nudges.append({"text": "Tell me which voice is you", "href": "/speakers", "icon": "⭐"})
    for s in enrolled:
        if not s["relationship"] and not (selfsp and s["id"] == selfsp["id"]):
            nudges.append({"text": f"Set how you know {s['label']}",
                           "href": f"/speakers/{s['id']}", "icon": "🔗"})
    # profiles refreshed in the last day → worth a glance
    for r in c.execute("SELECT speaker_id, COALESCE(sp.name,'Unknown_'||sp.id) AS label "
                       "FROM profiles p JOIN speakers sp ON sp.id=p.speaker_id "
                       "WHERE p.updated_at > datetime('now','-1 day') "
                       "ORDER BY p.updated_at DESC LIMIT 3").fetchall():
        nudges.append({"text": f"{r['label']}'s profile was just updated",
                       "href": f"/speakers/{r['speaker_id']}", "icon": "✨"})
    return nudges[:5]


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    c = db.connect()
    unknowns = db.unknown_speakers(c)
    samples = {r["id"]: db.speaker_segments(c, r["id"], limit=1) for r in unknowns}
    allopen = db.list_intents(c)
    hour = datetime.now(_CT).hour
    greeting = ("Good morning" if hour < 12 else
                "Good afternoon" if hour < 18 else "Good evening")
    return page("home.html", request, active="home", counts=db.counts(c),
                greeting=greeting, task_groups=_task_groups(allopen), open_count=len(allopen),
                review=_review_nudges(c), unknowns=unknowns, samples=samples,
                enrolled=db.enrolled_speakers(c), transcripts=db.recent_transcripts(c, 6))


@app.post("/tasks/add")
def add_task(action: str = Form("")):
    a = action.strip()
    if a:
        c = db.connect()
        sp = db.get_self(c)
        c.execute("INSERT INTO intents(speaker_id, action, kind, tier, status) "
                  "VALUES (?, ?, 'task', 'SOON', 'pending')", (sp["id"] if sp else None, a))
        c.commit()
        try:                                  # push to Google Tasks if connected
            google_sync.sync_pending(c)
        except Exception:  # noqa: BLE001
            pass
    return RedirectResponse("/", status_code=303)


@app.get("/speakers", response_class=HTMLResponse)
def speakers(request: Request):
    return page("speakers.html", request, active="speakers",
                speakers=db.list_speakers(db.connect()))


@app.get("/speakers/{sid}", response_class=HTMLResponse)
def speaker(request: Request, sid: int):
    c = db.connect()
    sp = db.get_speaker(c, sid)
    if not sp:
        raise HTTPException(404)
    return page("speaker.html", request, active="speakers", sp=sp,
                profile=db.get_profile(c, sid), tasks=db.speaker_intents(c, sid),
                segments=db.speaker_segments(c, sid))


@app.post("/speakers/{sid}/profiling")
def toggle_profiling(sid: int):
    c = db.connect()
    sp = db.get_speaker(c, sid)
    if sp:
        db.set_do_not_profile(c, sid, flag=not bool(sp["do_not_profile"]))
    return RedirectResponse(f"/speakers/{sid}", status_code=303)


@app.post("/speakers/{sid}/relationship")
def set_relationship(sid: int, relationship: str = Form("")):
    c = db.connect()
    rel = relationship.strip()
    if rel.lower() == "myself":
        db.set_self(c, sid)               # exclusive — clears 'myself' from all others
        db.set_relationship(c, sid, None)
    else:
        sp = db.get_speaker(c, sid)
        if sp and sp["is_self"]:
            c.execute("UPDATE speakers SET is_self=0 WHERE id=?", (sid,))
            c.commit()
        db.set_relationship(c, sid, rel or None)
    return RedirectResponse(f"/speakers/{sid}", status_code=303)


@app.post("/speakers/{sid}/delete")
def delete_speaker(sid: int):
    db.delete_speaker(db.connect(), sid)
    return RedirectResponse("/speakers", status_code=303)


@app.post("/speakers/{sid}/name")
def name_speaker(request: Request, sid: int, name: str = Form(...)):
    nm = name.strip()
    db.rename_speaker(db.connect(), sid, nm)
    if _hx(request):
        return HTMLResponse(f'<div class="card"><div class="empty">✓ Saved as '
                            f'<b>{html.escape(nm)}</b></div></div>')
    return RedirectResponse(f"/speakers/{sid}", status_code=303)


@app.post("/speakers/{sid}/merge")
def merge_speaker(request: Request, sid: int, target: int = Form(...)):
    db.merge_speakers(db.connect(), sid, target)
    who = db.get_speaker(db.connect(), target)
    nm = who["label"] if who else "speaker"
    if _hx(request):
        return HTMLResponse(f'<div class="card"><div class="empty">✓ Merged into '
                            f'<b>{html.escape(nm)}</b> — voiceprint improved</div></div>')
    return RedirectResponse("/unknown", status_code=303)


@app.get("/transcripts/{tid}", response_class=HTMLResponse)
def transcript(request: Request, tid: int):
    c = db.connect()
    t = db.transcript(c, tid)
    if not t:
        raise HTTPException(404)
    blocks = []
    for s in db.transcript_segments(c, tid):
        if not blocks or blocks[-1]["sid"] != s["speaker_id"]:
            blocks.append({"sid": s["speaker_id"], "who": s["who"], "lines": []})
        blocks[-1]["lines"].append({"t": s["t_start"], "text": s["text"]})
    return page("transcript.html", request, active=None, t=t, blocks=blocks,
                tags=db.transcript_tag_list(c, tid), all_tags=db.list_tags(c))


@app.get("/topics", response_class=HTMLResponse)
def topics(request: Request):
    return page("topics.html", request, active="topics", tags=db.list_tags(db.connect()))


@app.get("/topics/{ref}", response_class=HTMLResponse)
def topic(request: Request, ref: str):
    c = db.connect()
    tag = db.get_tag(c, ref)
    if not tag:
        raise HTTPException(404)
    return page("topic.html", request, active="topics", tag=tag,
                snippets=db.tag_transcripts(c, tag["id"]),
                all_tags=[t for t in db.list_tags(c) if t["id"] != tag["id"]])


@app.post("/transcripts/{tid}/tag")
def add_transcript_tag(tid: int, name: str = Form("")):
    c = db.connect()
    nm = name.strip().lower()
    if nm:
        db.tag_transcript(c, tid, db.get_or_create_tag(c, nm))
        c.commit()
    return RedirectResponse(f"/transcripts/{tid}", status_code=303)


@app.post("/transcripts/{tid}/untag/{tag_id}")
def remove_transcript_tag(tid: int, tag_id: int):
    db.untag_transcript(db.connect(), tid, tag_id)
    return RedirectResponse(f"/transcripts/{tid}", status_code=303)


@app.post("/topics/{tag_id}/rename")
def topic_rename(tag_id: int, name: str = Form("")):
    if name.strip():
        db.rename_tag(db.connect(), tag_id, name.strip().lower())
    return RedirectResponse(f"/topics/{tag_id}", status_code=303)


@app.post("/topics/{tag_id}/merge")
def topic_merge(tag_id: int, target: int = Form(...)):
    db.merge_tags(db.connect(), tag_id, target)
    return RedirectResponse(f"/topics/{target}", status_code=303)


@app.get("/unknown", response_class=HTMLResponse)
def unknown(request: Request):
    c = db.connect()
    rows = db.unknown_speakers(c)
    samples = {r["id"]: db.speaker_segments(c, r["id"], limit=3) for r in rows}
    return page("unknown.html", request, active="unknown", unknowns=rows,
                samples=samples, enrolled=db.enrolled_speakers(c))


@app.get("/tasks", response_class=HTMLResponse)
def tasks(request: Request):
    c = db.connect()
    return page("tasks.html", request, active="tasks",
                soon=db.list_intents(c, "SOON"), later=db.list_intents(c, "LATER"))


@app.post("/tasks/{iid}/dismiss")
def dismiss(request: Request, iid: int):
    c = db.connect()
    google_sync.remove_intent(c, iid)        # also delete the Calendar event / Task
    db.dismiss_intent(c, iid)
    if _hx(request):
        return HTMLResponse("")  # HTMX removes the row
    return RedirectResponse("/tasks", status_code=303)


@app.get("/activity", response_class=HTMLResponse)
def activity(request: Request):
    c = db.connect()
    since = db.meta_get(c, "activity_seen_at", "1970-01-01")
    data = db.activity_since(c, since)
    db.meta_set(c, "activity_seen_at", datetime.now(_UTC).strftime("%Y-%m-%d %H:%M:%S"))
    return page("activity.html", request, active="activity", new_count=0, since=since, **data)


ASSIST_SESSIONS = {}   # sid -> live conversation messages (ephemeral, in-memory)


@app.get("/assistant/stream")
async def assistant_stream(q: str, sid: str = ""):
    """SSE stream of the page assistant (tokens + tool-call events). `sid` keys an
    in-memory conversation so multi-turn context works; cleared on restart."""
    messages = ASSIST_SESSIONS.get(sid)
    if messages is None:
        messages = [{"role": "system", "content": assistant.SYSTEM}]
        if sid:
            if len(ASSIST_SESSIONS) > 50:          # bound memory across chats
                ASSIST_SESSIONS.clear()
            ASSIST_SESSIONS[sid] = messages
    messages.append({"role": "user", "content": q})
    if len(messages) > 25:                          # keep system + recent turns
        messages[:] = [messages[0]] + messages[-24:]
    return StreamingResponse(
        assistant.run(messages), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request, mail: str = ""):
    gpu_clear, gpu_detail = gpu_gate.peek()
    return page("settings.html", request, active="settings", mail=mail,
                mcp_running=mcp_mgr.running(), model=assistant.MODEL,
                worker_running=worker_mgr.running(), worker=_worker_status(),
                queue=db.queue_stats(db.connect()),
                gpu_clear=gpu_clear, gpu_detail=gpu_detail,
                asr_model=os.environ.get("LISTENER_ASR_MODEL", "large-v3"),
                google=google_sync.status(),
                mail_configured=mailer.configured(),
                mail_to=(os.environ.get("LISTENER_MAIL_TO")
                         or os.environ.get("LISTENER_SMTP_USER") or "—"),
                brief_time=f"{BRIEF_HOUR % 12 or 12}:{BRIEF_MIN:02d} PM")


@app.post("/settings/mcp/{action}")
def mcp_control(request: Request, action: str):
    if action in ("start", "restart"):
        mcp_mgr.start()
    elif action == "stop":
        mcp_mgr.stop()
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/worker/{action}")
def worker_control(action: str):
    if action in ("start", "restart"):
        worker_mgr.start()
    elif action == "stop":
        worker_mgr.stop()
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/mail/test")
def mail_test():
    try:
        ok = mailer.send("Listener test ✅",
                         "If you're reading this, email delivery works. 🎧")
        flag = "sent" if ok else "noconfig"
    except Exception as e:  # noqa: BLE001 — surface SMTP errors to the user
        print(f"mail test failed: {e}")
        flag = "error"
    return RedirectResponse(f"/settings?mail={flag}", status_code=303)


@app.get("/segment/{seg_id}/audio.wav")
def segment_audio(seg_id: int):
    s = db.get_segment(db.connect(), seg_id)
    if not s or not s["audio_path"] or not os.path.exists(s["audio_path"]):
        raise HTTPException(404, "audio unavailable")
    start = float(s["t_start"])
    dur = max(0.2, float(s["t_end"]) - start)
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(dur),
         "-i", s["audio_path"], "-ac", "1", "-ar", "16000", "-f", "wav", "pipe:1"],
        capture_output=True).stdout
    return Response(out, media_type="audio/wav")


# ---- device ingest (the ONLY publicly-exposed path; Funnel + HMAC) ----
@app.post("/ingest")
async def ingest(request: Request, x_sig: str = Header(""),
                 x_ts: str = Header(""), x_seq: str = Header("0")):
    body = await request.body()
    try:
        ts = int(x_ts)
    except ValueError:
        raise HTTPException(401, "bad ts")
    if abs(time.time() - ts) > 300:
        raise HTTPException(401, "stale timestamp")
    mac = hmac.new(INGEST_SECRET.encode(), f"{ts}".encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, x_sig):
        raise HTTPException(401, "bad signature")
    path = os.path.join(CHUNK_DIR, f"chunk_{ts}_{x_seq}.bin")
    with open(path, "wb") as f:
        f.write(body)
    c = db.connect()
    cur = c.cursor()
    cur.execute("INSERT INTO chunks(seq, ts_start, bytes, path, acked) VALUES (?,?,?,?,1)",
                (int(x_seq), str(ts), len(body), path))
    c.commit()
    return {"acked": cur.lastrowid}


@app.get("/healthz")
def healthz():
    return {"ok": True}
