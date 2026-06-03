# Homelab Pipeline (Python)

Runs on the i5 + RTX 4070 Super. Detailed code lands in Phase 4.

## Services (each can be a separate process / unit)
1. **Ingest API** (FastAPI/uvicorn)
   - `POST /ingest` — verify bearer token + HMAC + timestamp window, write the
     audio segment to the chunk store, return `{acked: seq}`.
   - Exposed to the device two ways: **LAN IP** (home) and **Tailscale Funnel**
     (away). Only this path is public; everything else stays on the tailnet.
2. **Transcriber** (worker)
   - Watches chunk store → **faster-whisper** on CUDA (decodes Opus/ADPCM via
     ffmpeg) → transcript + word timestamps → DB.
3. **Intent splitter** (LLM)
   - For each transcript segment, produce structured intents (schema below) and
     long-term context updates. Local model or Gemini API — see Q-S3.
4. **Scheduler / dispatcher** — see `SCHEDULING.md` (APScheduler).
5. **Dashboard (PWA)** — review/edit context + conversational edit agent.

## Data model (SQLite, draft)
```
chunks(id, device, seq, ts_start, codec, bytes, path, acked, transcribed)
transcripts(id, chunk_id, text, words_json, lang, created_at)
intents(id, transcript_id, action, tier, due_at, status, source_quote)
context(id, kind, subject, summary, emotion, updated_at)   -- long-term
people(id, name, relationship, notes, updated_at)           -- relational
schedule_jobs(...)   -- APScheduler's own job store table
```
`tier ∈ {SOON, LATER}`. `status ∈ {pending, scheduled, sent, dismissed}`.

## Intent schema (LLM output contract)
```json
{
  "intents": [
    {"action":"take out trash","tier":"SOON","due_at":"2026-06-02T19:00",
     "confidence":0.8,"source_quote":"please take out the trash tonight"}
  ],
  "context_updates": [
    {"kind":"relational","subject":"wife","summary":"...","emotion":"neutral"}
  ]
}
```
- SOON = needs action today / time-sensitive → goes to the scheduler.
- LATER = tomorrow+ or informational → folded into the daily summary.

## Email formatting (Gemini-friendly)
- Immediate emails: terse subject with a stable tag (e.g. `[ACTION] trash 7PM`),
  body with the single action + source quote + time, to maximize Workspace/Gemini
  pickup accuracy.
- Daily "Day Ahead" (6 AM): grouped sections (Today, Upcoming, Context notes).
- Exact tagging scheme + transport: **Q-S2**.

## Build-up milestones (all testable WITHOUT hardware — feed a recorded WAV)
| # | Milestone |
|---|-----------|
| H1 | WSL2 env + CUDA + faster-whisper transcribes a sample WAV |
| H2 | FastAPI `/ingest` — receive file, verify HMAC, store, return ACK |
| H3 | Transcriber worker — watch store → whisper → transcript to SQLite |
| H4 | LLM intent split → `{action, due_at, tier}` rows |
| H5 | APScheduler → timed email (e.g. 7 PM) + daily 6 AM summary (Gmail/SMTP) |
| H6 | PWA dashboard + conversational context editor |
- H1→H5 prove the whole transcribe→split→schedule→email flow before any board arrives.

## Security
- HMAC verify on ingest; reject stale timestamps (replay).
- Audio retention policy: **Q-S4**.
