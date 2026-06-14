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

from fastapi import FastAPI, Form, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import ClientDisconnect

import assistant
import backup
import db
import google_sync
import gpu_gate
import intents
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
BRIEF_HOUR, BRIEF_MIN = 23, 50   # default — 11:50 PM local, lands before midnight (ADR-024)


def _brief_hm(c=None):
    """The nightly-brief send time (h, m), user-configurable via meta (ADR-034)."""
    raw = db.meta_get(c or db.connect(), "brief_time", f"{BRIEF_HOUR:02d}:{BRIEF_MIN:02d}")
    try:
        h, m = (int(x) for x in str(raw).split(":")[:2])
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except (ValueError, TypeError):
        pass
    return BRIEF_HOUR, BRIEF_MIN


def _schedule_brief():
    """(Re)install the daily-brief cron job at the configured time."""
    h, m = _brief_hm()
    scheduler.add_job(mailer.send_daily_brief, CronTrigger(hour=h, minute=m),
                      id="daily_brief", replace_existing=True, misfire_grace_time=3600)


def _fmt_hm(h, m):
    return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


# Dashboard-tunable knobs (ADR-035). Defaults come from each module's constant so
# there's a single source of truth; the consumers read db.cfg(...) at runtime.
# (voice_match_threshold's default is mirrored here — speakerid.py can't be imported
# in the light web venv, but its MATCH_THRESHOLD is the same 0.40.)
TUNABLES = [
    ("Task intelligence", [
        {"key": "triage_threshold", "label": "Auto-add confidence", "default": intents.TRIAGE_THRESHOLD,
         "min": 0.3, "max": 1.0, "step": 0.05, "unit": "",
         "help": "How sure the AI must be before an item goes straight to your Calendar/Tasks. "
                 "Below this it waits in the Review queue. Higher = more held for review."},
        {"key": "close_threshold", "label": "Auto-complete confidence", "default": intents.CLOSE_THRESHOLD,
         "min": 0.3, "max": 1.0, "step": 0.05, "unit": "",
         "help": "How sure the AI must be that it heard a task finished before closing it out. "
                 "Higher = more cautious (fewer auto-completes)."},
        {"key": "dedupe_similarity", "label": "Duplicate sensitivity", "default": intents.SIM_THRESHOLD,
         "min": 0.5, "max": 1.0, "step": 0.02, "unit": "",
         "help": "How alike two tasks (same day) must be to count as the same thing. "
                 "Lower = merges more eagerly; higher = keeps near-duplicates separate."},
    ]),
    ("Calendar", [
        {"key": "event_duration_min", "label": "Default event length", "default": google_sync.EVENT_HOURS * 60,
         "min": 15, "max": 480, "step": 15, "unit": "min",
         "help": "Length of a calendar event when no end time is mentioned."},
        {"key": "event_reminder_min", "label": "Reminder lead time", "default": google_sync.REMIND_MIN,
         "min": 0, "max": 120, "step": 5, "unit": "min",
         "help": "How long before an event Google pops its reminder."},
    ]),
    ("Privacy & people", [
        {"key": "audio_retain_days", "label": "Audio retention", "default": purge.RETAIN_DAYS,
         "min": 1, "max": 365, "step": 1, "unit": "days",
         "help": "Days before recorded audio auto-deletes. Transcripts, profiles, and search are "
                 "always kept — only the playable audio is purged."},
        {"key": "voice_match_threshold", "label": "Voice-match strictness", "default": 0.40,
         "min": 0.2, "max": 0.8, "step": 0.02, "unit": "",
         "help": "How closely a voice must match an enrolled person to be tagged as them. Higher = "
                 "stricter (more new 'unknowns' to name); lower = more eager. New recordings only."},
    ]),
    ("Performance (gaming)", [
        {"key": "gpu_util_max", "label": "Pause above GPU load", "default": gpu_gate.UTIL_MAX_PCT,
         "min": 10, "max": 95, "step": 5, "unit": "%",
         "help": "Heavy processing pauses while the GPU is busier than this, so transcription "
                 "never fights a game for the card."},
        {"key": "gpu_free_min_mib", "label": "Min free VRAM", "default": gpu_gate.FREE_MIN_MIB,
         "min": 512, "max": 12000, "step": 256, "unit": "MB",
         "help": "Also pause if free VRAM drops below this (OOM guard). Advanced — leave as-is "
                 "unless you hit out-of-memory errors."},
    ]),
    ("Transcription", [
        {"key": "asr_no_speech_max", "label": "Drop-silence aggressiveness", "default": 0.6,
         "min": 0.1, "max": 0.95, "step": 0.05, "unit": "",
         "help": "Discard a transcribed segment if Whisper is at least this sure it's NOT "
                 "speech. Lower = filters more silence/noise (fewer phantom 'Thank you' lines)."},
        {"key": "asr_min_logprob", "label": "Min transcription confidence", "default": -1.0,
         "min": -3.0, "max": 0.0, "step": 0.1, "unit": "",
         "help": "Discard low-confidence segments (avg log-prob below this). Closer to 0 = stricter."},
    ]),
]
_TUNABLE_BY_KEY = {k["key"]: k for _, ks in TUNABLES for k in ks}


def _tuning_view(c):
    """Current value (+ whether it's the default) for each knob, grouped for the UI."""
    out = []
    for group, knobs in TUNABLES:
        items = []
        for k in knobs:
            val = db.cfg(c, k["key"], k["default"])
            items.append({**k, "value": val, "is_default": val == k["default"]})
        out.append((group, items))
    return out


def _flush_profiles():
    """Hourly debounced profile refresh (ADR-038) — only when the GPU is free, so it
    never competes with gaming or the live pipeline."""
    try:
        clear, _ = gpu_gate.peek()
        if not clear:
            return
        import profiles
        done = profiles.flush_dirty(db.connect())
        if done:
            print(f"profile flush: refreshed {len(done)} speaker(s)", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"profile flush error: {e}", flush=True)


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
    _schedule_brief()     # nightly brief at the user-configured time (ADR-034)
    scheduler.add_job(purge.purge_old_audio, CronTrigger(hour=3, minute=0),
                      id="audio_purge", replace_existing=True, misfire_grace_time=7200)
    scheduler.add_job(backup.make_backup, CronTrigger(hour=3, minute=30),
                      id="db_backup", replace_existing=True, misfire_grace_time=7200)
    scheduler.add_job(_flush_profiles, CronTrigger(minute=20),
                      id="profile_flush", replace_existing=True, misfire_grace_time=1800)
    scheduler.start()     # nightly brief + 3 AM purge + 3:30 AM DB backup (ADR-030)
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
    """Render a page, injecting the global nav badges + device alert every time."""
    try:
        c = db.connect()
        if "new_count" not in ctx:            # activity bell badge
            ctx["new_count"] = db.activity_count(
                c, db.meta_get(c, "activity_seen_at", "1970-01-01"))
        ctx.setdefault("review_count", len(db.suggested_intents(c)) + len(db.close_pending_intents(c)))
        ctx.setdefault("unknown_count", len(db.unknown_speakers(c)))
        ctx.setdefault("device_alert", _device_alert(c))
    except Exception:  # noqa: BLE001
        for k, v in (("new_count", 0), ("review_count", 0), ("unknown_count", 0),
                     ("device_alert", None)):
            ctx.setdefault(k, v)
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


def _setup_steps(c):
    """First-run checklist for the Home page; each step reads a live status."""
    g = google_sync.status()
    return [
        {"done": bool(db.get_self(c)), "icon": "⭐",
         "text": "Tell me which voice is you", "href": "/speakers"},
        {"done": bool(g.get("connected")), "icon": "📅",
         "text": "Connect Google Calendar & Tasks", "href": "/settings"},
        {"done": mailer.configured(), "icon": "✉️",
         "text": "Set up the nightly email brief", "href": "/settings"},
        {"done": bool(db.device_status_list(c)), "icon": "🎧",
         "text": "Flash & connect the wearable", "href": "/settings"},
    ]


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    c = db.connect()
    unknowns = db.unknown_speakers(c)
    samples = {r["id"]: db.speaker_segments(c, r["id"], limit=1) for r in unknowns}
    allopen = db.list_intents(c)
    hour = datetime.now(_CT).hour
    greeting = ("Good morning" if hour < 12 else
                "Good afternoon" if hour < 18 else "Good evening")
    recents = db.recent_transcripts(c, 6)
    setup = _setup_steps(c)
    return page("home.html", request, active="home", counts=db.counts(c),
                greeting=greeting, task_groups=_task_groups(allopen), open_count=len(allopen),
                setup=setup, setup_done=all(s["done"] for s in setup),
                review=_review_nudges(c), unknowns=unknowns, samples=samples,
                enrolled=db.enrolled_speakers(c), transcripts=recents,
                suggested=db.suggested_intents(c), close_pending=db.close_pending_intents(c),
                auto_closed=db.recent_auto_closed(c),
                rec_tags={r["id"]: db.transcript_tag_list(c, r["id"]) for r in recents})


@app.post("/tasks/add")
def add_task(action: str = Form("")):
    a = action.strip()
    if a:
        c = db.connect()
        intents.add_manual(c, a)              # LLM-classify (kind + due) like a spoken one
        try:                                  # push to Calendar/Tasks (ADR-026)
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


@app.post("/speakers/{sid}/profile")
def edit_speaker_profile(sid: int, summary: str = Form(""), traits: str = Form(""),
                         interests: str = Form(""), dislikes: str = Form(""),
                         notable: str = Form(""), dates: str = Form("")):
    def lines(s):
        return [x.strip() for x in s.splitlines() if x.strip()]

    def parse_dates(s):
        out = []
        for ln in lines(s):
            sep = "=" if "=" in ln else (":" if ":" in ln else "")
            label, _, d = ln.partition(sep) if sep else (ln, "", "")
            out.append({"label": label.strip(), "date": d.strip()})
        return out

    db.edit_profile(db.connect(), sid, summary=summary.strip(), traits=lines(traits),
                    interests=lines(interests), dislikes=lines(dislikes),
                    notable=lines(notable), dates=parse_dates(dates))
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
def topics(request: Request, t: list[int] = Query(default=[])):
    c = db.connect()
    all_tags = db.list_tags(c)
    if not t:
        return page("topics.html", request, active="topics", tags=all_tags, filtering=False)

    def url(ids):
        return "/topics" + ("?" + "&".join(f"t={i}" for i in ids) if ids else "")
    active_tags = [{"id": tg["id"], "name": tg["name"],
                    "remove_url": url([i for i in t if i != tg["id"]])}
                   for tg in all_tags if tg["id"] in t]
    other_tags = [{"id": tg["id"], "name": tg["name"], "n": tg["n"], "add_url": url(t + [tg["id"]])}
                  for tg in all_tags if tg["id"] not in t and tg["n"]]
    return page("topics.html", request, active="topics", tags=all_tags, filtering=True,
                active_tags=active_tags, other_tags=other_tags,
                snippets=db.transcripts_with_all_tags(c, t))


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


@app.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = ""):
    return page("search.html", request, active=None, q=q,
                results=db.search_transcripts(db.connect(), q))


@app.get("/export")
def export(ids: list[int] = Query(default=[])):
    c = db.connect()
    out = []
    for tid in ids:
        t = db.transcript(c, tid)
        if not t:
            continue
        out.append(f"# Conversation #{tid} — {t['created_at']}")
        tg = [x["name"] for x in db.transcript_tag_list(c, tid)]
        if tg:
            out.append("Topics: " + ", ".join("#" + x for x in tg))
        out.append("")
        for s in db.transcript_segments(c, tid):
            out.append(f"[{s['t_start']:.1f}s] {s['who']}: {s['text']}")
        out.append("\n---\n")
    body = "\n".join(out) or "No conversations selected."
    return Response(body, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=listener-export.md"})


@app.get("/unknown", response_class=HTMLResponse)
def unknown(request: Request):
    c = db.connect()
    rows = db.unknown_speakers(c)
    samples = {r["id"]: db.speaker_segments(c, r["id"], limit=3) for r in rows}
    return page("unknown.html", request, active="unknown", unknowns=rows,
                samples=samples, enrolled=db.enrolled_speakers(c))


@app.post("/unknown/batch")
async def unknown_batch(request: Request):
    """Assign many unknown voices in one go: per id, merge into an existing person
    (wins) or name as new. Empty rows are skipped."""
    form = await request.form()
    c = db.connect()
    merged = named = 0
    for r in db.unknown_speakers(c):
        sid = r["id"]
        target = (form.get(f"merge_{sid}") or "").strip()
        name = (form.get(f"name_{sid}") or "").strip()
        if target:
            try:
                db.merge_speakers(c, sid, int(target))
                merged += 1
            except (ValueError, TypeError):
                pass
        elif name:
            db.rename_speaker(c, sid, name)
            named += 1
    return RedirectResponse("/unknown", status_code=303)


@app.get("/tasks", response_class=HTMLResponse)
def tasks(request: Request):
    c = db.connect()
    return page("tasks.html", request, active="tasks",
                soon=db.list_intents(c, "SOON"), later=db.list_intents(c, "LATER"),
                suggested=db.suggested_intents(c), close_pending=db.close_pending_intents(c),
                auto_closed=db.recent_auto_closed(c))


@app.post("/tasks/{iid}/dismiss")
def dismiss(request: Request, iid: int):
    c = db.connect()
    google_sync.remove_intent(c, iid)        # also delete the Calendar event / Task
    db.dismiss_intent(c, iid)
    if _hx(request):
        return HTMLResponse("")  # HTMX removes the row
    return RedirectResponse("/tasks", status_code=303)


@app.post("/tasks/review/approve-all")
def approve_all(request: Request):
    """Bulk: add every triaged suggestion to Calendar/Tasks."""
    c = db.connect()
    db.approve_all_suggested(c)
    try:
        google_sync.sync_pending(c)
    except Exception:  # noqa: BLE001
        pass
    return RedirectResponse(request.headers.get("referer", "/"), status_code=303)


@app.post("/tasks/review/dismiss-all")
def dismiss_all(request: Request):
    """Bulk: dismiss every triaged suggestion."""
    db.dismiss_all_suggested(db.connect())
    return RedirectResponse(request.headers.get("referer", "/"), status_code=303)


@app.post("/tasks/{iid}/approve")
def approve_task(request: Request, iid: int):
    """Promote a triaged 'suggested' item to active → it then pushes to Google."""
    c = db.connect()
    db.approve_intent(c, iid)
    try:
        google_sync.sync_pending(c)
    except Exception:  # noqa: BLE001
        pass
    if _hx(request):
        return HTMLResponse("")
    return RedirectResponse("/", status_code=303)


@app.post("/tasks/{iid}/confirm-close")
def confirm_close_task(request: Request, iid: int):
    """User confirmed a suggested closure (events) → remove from Google + close out."""
    c = db.connect()
    google_sync.remove_intent(c, iid)
    db.close_intent(c, iid)                  # close_kind/note were set by the reconciler
    if _hx(request):
        return HTMLResponse("")
    return RedirectResponse("/", status_code=303)


@app.post("/tasks/{iid}/keep")
def keep_task(request: Request, iid: int):
    """User rejected a suggested closure — keep the item active."""
    db.keep_open(db.connect(), iid)
    if _hx(request):
        return HTMLResponse("")
    return RedirectResponse("/", status_code=303)


@app.post("/tasks/{iid}/undo")
def undo_task(request: Request, iid: int):
    """Reopen an auto-closed item; the next sync re-creates its Google item."""
    c = db.connect()
    db.undo_close(c, iid)
    try:
        google_sync.sync_pending(c)
    except Exception:  # noqa: BLE001
        pass
    if _hx(request):
        return HTMLResponse("")
    return RedirectResponse("/", status_code=303)


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


def _device_view(r):
    online, ago, secs = False, "—", None
    try:
        dt = datetime.fromisoformat(r["updated_at"]).replace(tzinfo=_UTC)
        secs = (datetime.now(_UTC) - dt).total_seconds()
        online = secs < 900
        ago = (f"{int(secs)}s ago" if secs < 60 else
               f"{int(secs // 60)}m ago" if secs < 3600 else
               f"{int(secs // 3600)}h ago" if secs < 86400 else f"{int(secs // 86400)}d ago")
    except (ValueError, TypeError):
        pass
    up = r["uptime_s"] or 0
    return {"id": r["device_id"], "online": online, "ago": ago, "secs": secs, "seq": r["seq"],
            "battery_pct": db.lipo_pct(r["battery_mv"]), "battery_mv": r["battery_mv"],
            "rssi": r["rssi"], "ssid": r["ssid"], "ip": r["ip"], "fw": r["fw"],
            "free_heap": r["free_heap"],
            "uptime": (f"{up // 3600}h {(up % 3600) // 60}m" if up else "—")}


def _device_alert(c):
    """A single banner-worthy device condition, or None. Low battery wins; otherwise
    a recently-active device that's gone quiet (not a long-idle test board)."""
    rows = db.device_status_list(c)
    if not rows:
        return None
    d = _device_view(rows[0])                       # most recently updated device
    if d["battery_pct"] is not None and d["battery_pct"] <= 15:
        return {"level": "warn", "icon": "🔋",
                "text": f"{d['id']} battery low — {d['battery_pct']}%"}
    if d["secs"] is not None and 900 < d["secs"] < 2 * 86400:   # quiet 15min–2 days
        return {"level": "muted", "icon": "📴",
                "text": f"{d['id']} hasn't checked in — last seen {d['ago']}"}
    return None


@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request, mail: str = ""):
    c = db.connect()
    gpu_clear, gpu_detail = gpu_gate.peek()
    bh, bm = _brief_hm(c)
    return page("settings.html", request, active="settings", mail=mail,
                devices=[_device_view(r) for r in db.device_status_list(c)],
                mcp_running=mcp_mgr.running(), model=assistant.MODEL,
                worker_running=worker_mgr.running(), worker=_worker_status(),
                queue=db.queue_stats(c),
                gpu_clear=gpu_clear, gpu_detail=gpu_detail,
                asr_model=os.environ.get("LISTENER_ASR_MODEL", "large-v3"),
                google=google_sync.status(),
                google_paused=not google_sync.sync_enabled(c),
                mail_configured=mailer.configured(),
                mail_to=(os.environ.get("LISTENER_MAIL_TO")
                         or os.environ.get("LISTENER_SMTP_USER") or "—"),
                brief_time=_fmt_hm(bh, bm), brief_time_val=f"{bh:02d}:{bm:02d}",
                tuning=_tuning_view(c))


@app.post("/settings/brief-time")
def set_brief_time(t: str = Form("")):
    """User-configurable nightly-brief send time (ADR-034)."""
    try:
        h, m = (int(x) for x in t.split(":")[:2])
        if 0 <= h < 24 and 0 <= m < 60:
            db.meta_set(db.connect(), "brief_time", f"{h:02d}:{m:02d}")
            _schedule_brief()
    except (ValueError, TypeError):
        pass
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/tuning")
async def save_tuning(request: Request):
    """Persist dashboard-edited tunables (ADR-035). Values at the default are cleared
    so `meta` only holds genuine overrides; everything is clamped to its range."""
    form = await request.form()
    c = db.connect()
    for key, spec in _TUNABLE_BY_KEY.items():
        if key not in form:
            continue
        try:
            val = type(spec["default"])(form[key])
        except (ValueError, TypeError):
            continue
        val = max(spec["min"], min(spec["max"], val))      # clamp into range
        if val == spec["default"]:
            db.cfg_clear(c, key)
        else:
            db.cfg_set(c, key, val)
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/tuning/reset")
def reset_tuning():
    c = db.connect()
    for key in _TUNABLE_BY_KEY:
        db.cfg_clear(c, key)
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/google/toggle")
def google_toggle():
    """Flip the Google-sync shutoff valve (ADR-034). Re-enabling flushes the backlog."""
    c = db.connect()
    now_off = not google_sync.sync_enabled(c)
    db.meta_set(c, "google_sync_enabled", "1" if now_off else "0")
    if now_off:                                  # just turned back ON → push what queued up
        try:
            google_sync.sync_pending(c)
        except Exception:  # noqa: BLE001
            pass
    return RedirectResponse("/settings", status_code=303)


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


# ---- device endpoints (publicly exposed via Funnel; HMAC-locked) ----
def _verify_sig(x_ts: str, x_sig: str, body: bytes) -> int:
    """Shared HMAC + replay-window check for the public device endpoints."""
    try:
        ts = int(x_ts)
    except ValueError:
        raise HTTPException(401, "bad ts")
    if abs(time.time() - ts) > 300:
        raise HTTPException(401, "stale timestamp")
    mac = hmac.new(INGEST_SECRET.encode(), f"{ts}".encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, x_sig):
        raise HTTPException(401, "bad signature")
    return ts


@app.post("/ingest")
async def ingest(request: Request, x_sig: str = Header(""), x_ts: str = Header(""),
                 x_seq: str = Header("0"), x_mark: str = Header("0")):
    # X-Mark: firmware sets this (e.g. "1") when the user pressed the REC/"remember"
    # button during this chunk → its items are deliberate captures (ADR-038).
    try:
        body = await request.body()
    except ClientDisconnect:                  # device/Funnel dropped mid-upload — not an error
        raise HTTPException(400, "client disconnected")
    if len(body) > 25 * 1024 * 1024:          # size cap on the public endpoint
        raise HTTPException(413, "too large")
    ts = _verify_sig(x_ts, x_sig, body)
    marked = 1 if str(x_mark).strip() not in ("", "0", "false", "False") else 0
    path = os.path.join(CHUNK_DIR, f"chunk_{ts}_{x_seq}.bin")
    with open(path, "wb") as f:
        f.write(body)
    c = db.connect()
    cur = c.cursor()
    cur.execute("INSERT INTO chunks(seq, ts_start, bytes, path, acked, marked) VALUES (?,?,?,?,1,?)",
                (int(x_seq), str(ts), len(body), path, marked))
    c.commit()
    return {"acked": cur.lastrowid}


@app.post("/telemetry")
async def telemetry(request: Request, x_sig: str = Header(""), x_ts: str = Header("")):
    """Periodic device status (battery, signal, uptime…). Tiny + signed; sent more
    often than audio. Stores the latest snapshot per device (ADR-031)."""
    try:
        body = await request.body()
    except ClientDisconnect:
        raise HTTPException(400, "client disconnected")
    if len(body) > 4096:
        raise HTTPException(413, "too large")
    _verify_sig(x_ts, x_sig, body)
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        raise HTTPException(400, "bad json")
    db.upsert_device_status(db.connect(), data)
    return {"ok": True}


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ---- PWA: manifest + service worker (ADR-036). SW served from root so its scope
# is the whole app; manifest gets the correct content-type for installability. ----
@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(os.path.join(HERE, "static", "manifest.webmanifest"),
                        media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(os.path.join(HERE, "static", "sw.js"),
                        media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})
