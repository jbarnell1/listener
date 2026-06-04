#!/usr/bin/env python3
"""Listener dashboard + ingest (FastAPI + HTMX).  ADR-019.

Runs in ~/listener-web. Tailnet-only dashboard (Tailscale Serve); only /ingest is
exposed publicly (Tailscale Funnel), HMAC + replay-window locked.

    cd /mnt/c/Listener/homelab
    ~/listener-web/bin/uvicorn app:app --host 0.0.0.0 --port 8000 --reload
"""
import hashlib
import hmac
import html
import os
import subprocess
import time

from fastapi import FastAPI, Form, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import db

HERE = os.path.dirname(os.path.abspath(__file__))
CHUNK_DIR = os.path.join(HERE, "data", "chunks")
os.makedirs(CHUNK_DIR, exist_ok=True)
INGEST_SECRET = os.environ.get("LISTENER_INGEST_SECRET", "dev-secret-change-me")

app = FastAPI(title="Listener")
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


tpl.env.filters["initials"] = _initials
tpl.env.filters["hue"] = _hue


def page(name, request, **ctx):
    return tpl.TemplateResponse(request, name, ctx)


def _hx(request):
    return request.headers.get("HX-Request") == "true"


# ---- dashboard (tailnet-only) ----
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    c = db.connect()
    return page("home.html", request, active="home", counts=db.counts(c),
                transcripts=db.recent_transcripts(c), speakers=db.list_speakers(c))


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
                segments=db.speaker_segments(c, sid))


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
    return page("transcript.html", request, active=None, t=t, blocks=blocks)


@app.get("/unknown", response_class=HTMLResponse)
def unknown(request: Request):
    c = db.connect()
    rows = db.unknown_speakers(c)
    samples = {r["id"]: db.speaker_segments(c, r["id"], limit=3) for r in rows}
    return page("unknown.html", request, active="unknown", unknowns=rows,
                samples=samples, enrolled=db.enrolled_speakers(c))


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
