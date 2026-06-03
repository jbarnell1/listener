# Homelab Pipeline (Python)

Runs on the i5 + RTX 4070 Super. Detailed code lands in Phase 4.

## Services (each can be a separate process / unit)
1. **Ingest API** (FastAPI/uvicorn)
   - `POST /ingest` — verify bearer token + HMAC + timestamp window, write the
     audio segment to the chunk store, return `{acked: seq}`.
   - Exposed to the device two ways: **LAN IP** (home) and **Tailscale Funnel**
     (away). Only this path is public; everything else stays on the tailnet.
2. **Transcriber + diarizer** (worker, GPU) — core, ADR-014
   - **pyannote.audio** diarizes turns; **faster-whisper** transcribes (decodes
     Opus/ADPCM via ffmpeg) with word timestamps; words align to turns →
     **speaker-attributed transcript segments** in DB.
3. **Speaker ID + profiler** (worker, GPU) — core, ADR-014
   - Per turn, **SpeechBrain ECAPA-TDNN** → 192-d embedding. Cosine-match vs the
     **known-voice library**; above threshold (Q-S6) → tag that `speaker`, else
     online-cluster into an `Unknown #N`.
   - Update each speaker's **relational profile** (topics, emotion trend, recurring
     asks, last-seen/frequency). Named via the dashboard (auto-cluster → label).
4. **Intent splitter** (LLM) — now **speaker-aware**
   - Per speaker-attributed segment, produce structured intents (schema below) +
     profile/context updates, attributing to the speaker. Local model or Gemini — Q-S3.
5. **Scheduler / dispatcher** — see `SCHEDULING.md` (APScheduler).
6. **Dashboard (PWA)** — label unknown speaker clusters, review/edit profiles +
   context, conversational edit agent.

## Data model (SQLite, draft)
```
chunks(id, device, seq, ts_start, codec, bytes, path, acked, transcribed)
transcripts(id, chunk_id, text, words_json, lang, created_at)
segments(id, transcript_id, speaker_id, t_start, t_end, text)  -- speaker-attributed
speakers(id, name, relationship, status, do_not_profile, created_at, updated_at)
                                  -- status ∈ {enrolled, unknown}; name null until labeled
embeddings(id, speaker_id, vec BLOB, source_chunk, created_at) -- ECAPA 192-d; centroid+samples
profiles(speaker_id, summary, emotion_trend, topics_json, recurring_json,
         last_seen, interaction_count, updated_at)             -- relational profile
intents(id, segment_id, speaker_id, action, tier, due_at, status, source_quote)
schedule_jobs(...)   -- APScheduler's own job store table
```
`tier ∈ {SOON, LATER}`. `status ∈ {pending, scheduled, sent, dismissed}`.
(`speakers`+`profiles` replace the earlier flat `people`/`context` tables.)

## Speaker enrollment & clustering (ADR-014)
1. Each turn's ECAPA embedding is cosine-matched to enrolled speakers' centroids.
2. **Match ≥ threshold (Q-S6):** tag that speaker; fold the embedding into its
   centroid (online update).
3. **No match:** assign to / start an `Unknown #N` cluster (online agglomerative).
4. **Dashboard:** unknown clusters show with sample snippets → you label once
   ("Unknown B = Sarah, wife") → cluster becomes that enrolled speaker; future
   audio auto-tags by name.
5. A `do_not_profile` flag stops building a profile for a given person (Q-S5).

## Intent schema (LLM output contract)
```json
{
  "intents": [
    {"action":"take out trash","tier":"SOON","due_at":"2026-06-02T19:00",
     "speaker":"wife","confidence":0.8,"source_quote":"please take out the trash tonight"}
  ],
  "profile_updates": [
    {"speaker":"wife","summary":"...","emotion":"neutral","topics":["chores"]}
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
| H3 | Transcriber worker — whisper → transcript to SQLite |
| **H3.5** | **pyannote diarization** → speaker-attributed `segments` |
| **H3.6** | **ECAPA embeddings + ID/cluster** → `speakers`/`embeddings`; profiles |
| H4 | **Speaker-aware** LLM intent split → `intents` (+ profile_updates) |
| H5 | APScheduler → timed email (e.g. 7 PM) + daily 6 AM summary (Gmail/SMTP) |
| H6 | PWA dashboard — label unknown clusters, review profiles, conversational editor |
- H1→H5 prove the whole transcribe→diarize→ID→split→schedule→email flow before any
  board arrives (all testable with a recorded WAV).
- **Setup note (Q-S7):** pyannote models are gated — need a HuggingFace account +
  accept the model terms to download.

## Security
- HMAC verify on ingest; reject stale timestamps (replay).
- Audio retention policy: **Q-S4**.
