# Listener Homelab Pipeline

Python services on **WSL2 (Ubuntu) + RTX 4070 Super**. **Fully local** for privacy
(ADR-016). Architecture + milestones: [`../docs/homelab/PIPELINE.md`](../docs/homelab/PIPELINE.md).

## One-time environment setup
Everything runs in WSL2. We use **`uv`** for packaging and keep the venv in WSL's
*native* filesystem (not `/mnt/c` — big CUDA wheels are slow/finicky on the Windows drive).

```bash
# 1. install uv (once)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. transcription venv (faster-whisper / CUDA 12)
uv venv ~/listener-venv --python 3.10
cd /mnt/c/Listener/homelab
uv pip install --python ~/listener-venv/bin/python -r requirements.txt

# 3. diarization venv (pyannote + torch / CUDA 13) — SEPARATE on purpose
uv venv ~/listener-diar --python 3.10
uv pip install --python ~/listener-diar/bin/python -r requirements-diar.txt
sudo apt install -y ffmpeg   # torchcodec needs the libav* libs
```

> **Why two venvs?** CTranslate2 (faster-whisper) needs **CUDA 12** cuDNN; torch
> (pyannote) needs **CUDA 13** cuDNN. In one venv they collide
> (`CUDNN_STATUS_SUBLIBRARY_VERSION_MISMATCH`). Separate venvs = no conflict, and
> it matches the production design (transcriber + diarizer are separate workers).
>
> GPU note: faster-whisper's cuBLAS/cuDNN come from `nvidia-*` pip wheels — **no
> system CUDA toolkit needed**; `transcribe.py` `ctypes`-preloads them (no
> `LD_LIBRARY_PATH`). Requires the NVIDIA CUDA-on-WSL driver (`nvidia-smi` works in WSL).
>
> Diarization needs a HuggingFace token (`hf auth login`) + accepting the gated
> model `pyannote/speaker-diarization-community-1`.

## H1 — transcription ✅
```bash
# fetch the sample once (gitignored):
wget -O samples/jfk.wav https://github.com/ggerganov/whisper.cpp/raw/master/samples/jfk.wav

cd /mnt/c/Listener/homelab
~/listener-venv/bin/python transcribe.py samples/jfk.wav small.en
```
Expected: the JFK quote, **RTF ≈ 0.05 (~20× real-time)** on GPU. Swap `small.en`
→ `large-v3` for production accuracy (one-word change).

## H3.5 — diarization ✅
```bash
cd /mnt/c/Listener/homelab
~/listener-diar/bin/python diarize.py samples/jfk.wav
```
Prints `SPEAKER_00 / SPEAKER_01 / ...` turns with timestamps. JFK (1 speaker) →
one speaker; use a 2-person clip to see it split. Uses `community-1` (open, but
gated — accept its HF terms once).

**Speaker-attributed transcript** (merge of transcription + diarization):
```bash
~/listener-venv/bin/python attribute.py samples/two.wav small.en
```
`attribute.py` runs both engines as subprocesses and merges by *segment* overlap.
**Superseded by word-level (ADR-022):** `wordattribute.py` uses **WhisperX** forced
alignment (`~/listener-wx` venv, `requirements-wx.txt`) for per-word timestamps,
assigns each word to its diarization turn, and regroups — so segments split exactly
at speaker changes:
```bash
uv venv ~/listener-wx --python 3.10
uv pip install --python ~/listener-wx/bin/python -r requirements-wx.txt
~/listener-web/bin/python wordattribute.py samples/two.wav small.en
```

## H3.6 — speaker identity (ECAPA) ✅
ECAPA 192-d voiceprints + a tiny JSON library name → centroid (`speakers.json`,
gitignored — it's biometric). All in the **diarization venv**.
```bash
# enroll a known voice once:
~/listener-diar/bin/python enroll.py Jon samples/jon.wav
# diarize + identify (names, not SPEAKER_xx):
~/listener-diar/bin/python identify.py samples/two.wav
# full named transcript (attribute.py now calls identify.py):
~/listener-venv/bin/python attribute.py samples/two.wav small.en
```
`embed.py fileA fileB` prints cosine similarity (same speaker ~0.5+, different
~<0.3). Match threshold = 0.40 (Q-S6, in `speakerid.py`). Unknown speakers get
`Unknown_xx` → label later in the dashboard → auto-recognized after.

## Storage — SQLite (`listener.db`, gitignored: transcripts + voiceprints)
Schema in `db.py` (full PIPELINE.md model: speakers, embeddings, chunks,
transcripts, segments, profiles, intents). Voiceprints are float32 BLOBs in
`embeddings`; unknown voices auto-persist as `status='unknown'` speakers so the
same voice re-matches across recordings (`rename()` to label them).
```bash
~/listener-diar/bin/python db.py        # init + row-count summary
# attribute.py now PERSISTS each run (transcript + speaker-linked segments):
~/listener-venv/bin/python attribute.py samples/two.wav small.en
~/listener-venv/bin/python show.py      # read latest transcript back (JOIN names)
```
Why not a vector DB? Speaker matching is cosine over a handful of people — instant
in numpy. SQLite is the right tool until there are thousands of speakers.

## Dashboard + ingest — FastAPI + HTMX (ADR-019)
Light venv (`~/listener-web`, no ML deps). Tailnet-only dashboard + HMAC `/ingest`.
```bash
uv venv ~/listener-web --python 3.10
uv pip install --python ~/listener-web/bin/python -r requirements-web.txt
cd /mnt/c/Listener/homelab
~/listener-web/bin/uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```
Open **http://localhost:8000** (WSL forwards to Windows). Pages: home, `/speakers`,
`/transcripts/{id}` (named segments), `/unknown` (snippet playback + name/tag),
`/activity` (🔔 "what's new since last check" — ADR-028). The Tasks page shows where each
item was routed (Calendar/Task/digest); dismissing one also deletes its Google item.
`/segment/{id}/audio.wav` slices audio on demand (404 after the 30-day purge).
`POST /ingest` verifies `X-Sig` HMAC-SHA256(secret, ts+body) + 5-min replay window.

## Page assistant — MCP + Ollama, streamed (ADR-020)
A real **MCP server** (`mcp_server.py`, FastMCP streamable-HTTP on :8765) exposes
the dashboard tools (`assistant_tools.py`) — full site parity: overview, recent
activity, speakers (list/get/profile/rename/**set relationship & "myself"**/**profiling
opt-out**/merge), unknowns, tasks (list with routing + dismiss → also deletes the
Google item), transcripts, and Google connection status. *Speaker delete stays
UI-only.* The app launches + restarts it (Settings page). The
assistant (`assistant.py`) connects to it **as an MCP client**, runs an Ollama
tool-calling agent loop, and **streams** tokens + tool-call events over SSE
(`GET /assistant/stream?q=…`). The mobile UI has a ✨ FAB → collapsible chat that
renders the stream. Model: **qwen3:8b** (qwen3:4b loops on multi-tool flows). All local.

## Speaker profiles — continuously enriched, + privacy delete (ADR-023)
`profile.py` runs a local-LLM pass over each new transcript and **merges** what it
learns about every named speaker into an evolving dossier (summary, relationship,
emotional trend, recurring topics/habits, durable facts) — it never starts from
scratch, so it compounds. Hooked into `intents.py` (rides the same per-transcript
LLM pass); honors the per-speaker opt-out (`speakers.do_not_profile`). Shown as a
card on the speaker page; the assistant reads it via `get_speaker_profile` (by name
or id). Backfill existing speakers: `python profile.py --backfill`.

The speaker page also has a **privacy delete** (`db.delete_speaker`, smart cascade):
removes their tasks, profile, voiceprint, and their lines in every transcript;
transcripts left empty (and their audio) are removed, shared conversations keep the
other speakers. UI-only with a confirm — deliberately **not** an assistant tool.

## Pipeline worker — ingest queue → end-to-end, GPU-gated (ADR-025)
`/ingest` stores each uploaded chunk as a queue item (`transcribed=0`) and ACKs
instantly. `worker.py` drains the queue: ffmpeg-normalize → `wordattribute`
(WhisperX + diarize + ID) → `intents` → `profiles` → done — one chunk at a time,
and only when the **GPU gate** (`gpu_gate.py`, reads the Windows `nvidia-smi.exe`)
is clear, so it never fights a game (ADR-015). Backlog self-heals after downtime.
Managed like the MCP server (auto-starts; restart/stop from Settings). Default ASR
model `large-v3` (`LISTENER_ASR_MODEL`). Drive it without hardware:
```bash
python ingest_send.py samples/two.wav    # sign + POST a WAV like the device will
python worker.py --once samples/two.wav  # or process one file directly
```

## Google Calendar + Tasks — reminders via Google, not timed emails (ADR-026)
The worker routes each extracted intent by `kind`: **events** → a Google **Calendar**
event (exact time + popup reminder), **to-dos** → a Google **Task** (date due — the
Tasks API discards time-of-day), **undated follow-ups** → the nightly email digest.
Google fires the reminders (and Gemini reads them), so the homelab needn't be awake at
reminder time. Auth is **OAuth**, not the SMTP app password:
1. GCP project → enable **Google Calendar API** + **Google Tasks API**.
2. OAuth consent screen → External, **Published** (Testing-mode tokens expire in 7 days).
3. Create an OAuth **Desktop** client → download JSON to `~/.listener-gcp/client_secret.json`.
4. `python google_sync.py --auth` (open the printed URL, authorize), then `--status`.

**Remote (phone) access — Tailscale Serve (tailnet-only, no public exposure):**
```bash
tailscale serve --bg 8000      # → https://<machine>.<tailnet>.ts.net
# later, expose ONLY ingest publicly for away-uploads:
# tailscale funnel --set-path /ingest --bg 8000
```

## Milestone status (PIPELINE.md H1–H6)
- [x] **H1** — WSL2 + CUDA + faster-whisper transcribes a WAV
- [ ] H2 — FastAPI `/ingest` (verify HMAC, store, ACK)
- [ ] H3 — transcriber worker → SQLite
- [x] **H3.5** — pyannote diarization (community-1, GPU)
- [x] **H3.6** — ECAPA embeddings + speaker ID (enroll/identify, named transcript)
- [ ] H4 — speaker-aware LLM intent split (Ollama; model from 12GB shortlist)
- [ ] H5 — APScheduler timed email + daily 6 AM summary
- [ ] H6 — PWA dashboard (label speakers, review profiles)
