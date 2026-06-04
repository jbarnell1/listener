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
4. **Intent splitter** (LLM) — speaker-aware, **fully local** (ADR-016)
   - Per speaker-attributed segment, produce structured intents (schema below) +
     profile updates, attributing to the speaker. **Ollama** with JSON/grammar-
     constrained output. Model TBD from a 12GB shortlist (Phi-4 Reasoning 14B /
     Qwen3 14B / Mistral Small 3 7B / Gemma 3 12B) — benchmark + pick.
5. **Scheduler / dispatcher** — see `SCHEDULING.md` (APScheduler).
6. **Dashboard (PWA)** — label unknown speaker clusters, review/edit profiles +
   context, conversational edit agent.

> **Environments gotcha:** transcription (CTranslate2 / CUDA 12) and diarization
> + torch (CUDA 13) run in **separate venvs / processes** — their cuDNN versions
> collide in one env (`CUDNN_STATUS_SUBLIBRARY_VERSION_MISMATCH`). Matches the
> separate-workers design. Setup in `homelab/README.md`.

## Data model (SQLite — implemented in `homelab/db.py`)
> Status: schema live; `speakers`/`embeddings` populated (ECAPA voiceprints as
> float32 BLOBs); `attribute.py` persists `transcripts`/`segments`. `chunks`/
> `profiles`/`intents` defined, populated as H2/H4 land.
```
chunks(id, device, seq, ts_start, codec, bytes, path, acked, transcribed)
transcripts(id, chunk_id, text, words_json, lang, created_at)
segments(id, transcript_id, speaker_id, t_start, t_end, text)  -- speaker-attributed
speakers(id, name, relationship, status, do_not_profile, is_self, created_at, updated_at)
                  -- status ∈ {enrolled, unknown}; name null until labeled; is_self = device owner
embeddings(id, speaker_id, vec BLOB, source_chunk, created_at) -- ECAPA 192-d; centroid+samples
profiles(speaker_id, summary, emotion_trend(=transient mood), traits_json, interests_json,
         dislikes_json, dates_json, notable_json, last_seen, interaction_count, updated_at)
                  -- personality/relational profile, ADR-023 (durable fields + transient mood)
intents(id, segment_id, speaker_id, action, kind, tier, due_at, status, source_quote,
        calendar_event_id, calendar_link, gtask_id, synced_at)  -- kind/Google sync ADR-026
tags(id, name, summary, ...)  +  transcript_tags(transcript_id, tag_id)  -- topics, ADR-029
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
5. A `do_not_profile` flag stops building a profile for a given person; a per-person
   privacy delete removes everything tied to them (see ADR-023). Profiles are
   continuously LLM-enriched per transcript (`profile.py`).

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

### Time handling (ADR-017)
- Prompt gives the model `current_local` + IANA tz `America/Chicago`.
- Model emits a **local** `due_local` / `due_text` — **no UTC/offset math** (LLMs are
  weak at it). **Code** resolves to UTC via `dateparser`/`zoneinfo` (RELATIVE_BASE=now,
  PREFER_DATES_FROM=future); the IANA zone handles CST/CDT automatically (no manual DST).
- **Store UTC** in the DB; render Central in emails. `due_local` kept as a cross-check.

## Email formatting (Gemini-friendly)
- Immediate emails: terse subject with a stable tag (e.g. `[ACTION] trash 7PM`),
  body with the single action + source quote + time, to maximize Workspace/Gemini
  pickup accuracy.
- Nightly **daily brief** (23:50 local): grouped sections (Soon, Coming up); text +
  HTML. Timed before midnight so the next-morning Google Daily Brief captures it.
- Transport = local SMTP + Gmail App Password (see ADR-024); `mailer.py`.

## Build-up milestones (all testable WITHOUT hardware — feed a recorded WAV)
| # | Milestone |
|---|-----------|
| H1 | WSL2 env + CUDA + faster-whisper transcribes a sample WAV |
| **H2** | **/ingest** verifies HMAC, stores the chunk as a queue item, ACKs instantly (ADR-025) |
| **H3** | **worker.py** drains the queue end-to-end (normalize→wordattribute→intents→profiles), GPU-gated (ADR-025) |
| **H3.5** | **pyannote diarization** → speaker-attributed `segments` |
| **H3.6** | **ECAPA embeddings + ID/cluster** → `speakers`/`embeddings`; profiles |
| H4 | **Speaker-aware** LLM intent split → `intents` (+ profile_updates) |
| H5 | APScheduler → nightly 23:50 daily brief over SMTP (ADR-024); per-action timed email next |
| H6 | PWA dashboard — label unknown clusters, review profiles, conversational editor |
- H1→H5 prove the whole transcribe→diarize→ID→split→schedule→email flow before any
  board arrives (all testable with a recorded WAV).
- **Setup note (Q-S7):** pyannote models are gated — need a HuggingFace account +
  accept the model terms to download.

## GPU-aware processing gate (ADR-015) — shares the card with gaming
The **heavy lane** (whisper, pyannote, ECAPA, LLM) is GPU-bound; the homelab also
games. Gate it so games win:
- **On-demand check, not continuous polling.** Before grabbing a job (worker idle),
  read GPU state once. If busy → sleep ~10–30 min, re-check.
- **Source the host truth:** call the **Windows `nvidia-smi.exe`** via WSL interop
  (the Linux WSL `nvidia-smi` may not see host graphics apps).
- **Defer if** `free_VRAM < ~6 GB` **OR** `avg_util > ~40%` (VRAM is the more reliable
  "game resident" signal — catches frame-capped games util misses).
- **Resume** only after the clear condition holds ~60 s (hysteresis; sampled @2 s).
- Check **only when idle** so the pipeline never gates itself (jobs are <1 min → yields
  within a minute of a game launching).
- Backlog self-heals at the **3 AM** idle window, before the 6 AM summary.
- **CPU work never pauses** (ingest, APScheduler, email) → **timely emails fire even
  mid-game.** No fixed quiet-hours cron needed.

## Security
- HMAC verify on ingest; reject stale timestamps (replay).
- Audio retention policy: **Q-S4**. Voice profiles/embeddings are **local-only**
  (ADR-016) — never leave the homelab.
