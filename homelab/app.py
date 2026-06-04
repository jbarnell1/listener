#!/usr/bin/env python3
"""Listener dashboard + ingest (FastAPI + HTMX).  ADR-019.

Runs in ~/listener-web. Tailnet-only dashboard (Tailscale Serve); only /ingest is
exposed publicly (Tailscale Funnel), HMAC + replay-window locked.

    cd /mnt/c/Listener/homelab
    ~/listener-web/bin/uvicorn app:app --host 0.0.0.0 --port 8000
"""
import hashlib
import hmac
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


def page(name, request, **ctx):
    return tpl.TemplateResponse(request, name, ctx)  # Starlette: (request, name, context)


# ---- dashboard (tailnet-only) ----
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    c = db.connect()
    return page("home.html", request, counts=db.counts(c),
                transcripts=db.recent_transcripts(c), speakers=db.list_speakers(c))


@app.get("/speakers", response_class=HTMLResponse)
def speakers(request: Request):
    return page("speakers.html", request, speakers=db.list_speakers(db.connect()))


@app.get("/speakers/{sid}", response_class=HTMLResponse)
def speaker(request: Request, sid: int):
    c = db.connect()
    sp = db.get_speaker(c, sid)
    if not sp:
        raise HTTPException(404)
    return page("speaker.html", request, sp=sp, segments=db.speaker_segments(c, sid))


@app.post("/speakers/{sid}/name")
def name_speaker(sid: int, name: str = Form(...)):
    db.rename_speaker(db.connect(), sid, name.strip())
    return RedirectResponse(f"/speakers/{sid}", status_code=303)


@app.get("/transcripts/{tid}", response_class=HTMLResponse)
def transcript(request: Request, tid: int):
    c = db.connect()
    t = db.transcript(c, tid)
    if not t:
        raise HTTPException(404)
    return page("transcript.html", request, t=t, segments=db.transcript_segments(c, tid))


@app.get("/unknown", response_class=HTMLResponse)
def unknown(request: Request):
    c = db.connect()
    rows = db.unknown_speakers(c)
    samples = {r["id"]: db.speaker_segments(c, r["id"], limit=3) for r in rows}
    return page("unknown.html", request, unknowns=rows, samples=samples)


@app.get("/segment/{seg_id}/audio.wav")
def segment_audio(seg_id: int):
    """Slice a segment's audio on demand (ffmpeg). 404 once the chunk is purged
    after 30 days (ADR-021)."""
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
    if abs(time.time() - ts) > 300:                       # replay window
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
