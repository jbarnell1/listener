# Decision Log (ADRs) & Open Questions

Short, dated records of *why* we chose something. Newest first. When a decision
is reversed, add a new entry rather than editing the old one.

## Decisions

### ADR-031 — Device telemetry (status + battery)
**2026-06-05.** The wearable periodically reports health to the homelab on a signed
**`/telemetry`** endpoint (same HMAC scheme as `/ingest`, shared `_verify_sig`; Funnel-
exposed on :8443). Tiny JSON — `battery_mv, rssi, ssid, ip, uptime_s, free_heap, fw,
seq` — sent **more often than audio** (~5 min vs the 15–30 min audio bursts) since it's
cheap. The homelab stores the latest snapshot per device (`device_status` table) and
shows a **Device card** on Settings: online/last-seen, battery % (+bar+voltage), WiFi +
signal, IP, uptime, heap, firmware. **Battery % is computed server-side** from the raw
mV via a LiPo curve (`db.lipo_pct`) so it's tunable without reflashing; the board's ÷2
divider (R7=R8=220 kΩ) feeds VBAT→GPIO7 (**ADC1**, chosen over ADC2 which conflicts with
WiFi). Rough voltage→% is fine for a low-battery warning; a **MAX17048 fuel gauge** is
the v2 accuracy upgrade. Future fields worth adding: charging state (TP4056 STAT→GPIO),
buffered-but-unsent chunk count, last-upload time, time-to-empty from the discharge slope.

### ADR-030 — Durability: rotating DB backups + logon auto-start
**2026-06-04.** The homelab is about to be always-on, so two durability gaps close.
(1) **Backups** (`backup.py`): a daily 3:30 AM job snapshots `listener.db` via SQLite's
online backup API (consistent under WAL) into `backups/` (gitignored), keeping the last
14; offsite mirroring to **Backblaze B2** (or any rclone remote) is one env var away
(`LISTENER_B2_REMOTE`). Rationale: the DB is the entire memory and a single file — local
rotation guards corruption/accidental deletion, B2 adds offsite (disk/PC loss). (2)
**Auto-start on reboot**: a hidden `.vbs` in the user's **Startup folder** runs
`listener.sh up` at logon (Task Scheduler needs admin; the Startup folder doesn't) — for
fully headless recovery, enable Windows auto-login. Also shipped: full-text **search**
across all spoken text (`/search`) and multi-select **export** of conversations to
Markdown (`/export`, from the topic + multi-tag-filter views).

### ADR-029 — Conversations organized by multi-label topic tags
**2026-06-04.** Conversations are inherently multi-subject and arrive as snippets
through the day, so rather than threading snippets into single-topic "conversations,"
each transcript gets **multiple topic tags** (`tags` + `transcript_tags`) — the same
snippet can live under several topics ("house hunting" AND "in-law troubles"). A
local-LLM pass (`tagger.py`, one call per snippet) assigns tags (reusing existing
topics or coining new ones) and returns each topic's updated **running summary**,
which compounds like profiles (ADR-023). The dashboard browses by topic (`/topics`,
`/topics/{tag}` = summary + every snippet over time); snippets show tag chips with
inline add/remove; topics can be renamed or **merged** (the "join" op — re-tagging a
snippet is the "split"). The assistant queries it via MCP (`list_tags` + `get_topic`)
to answer "what did we decide about <subject>". Chosen over single-topic threading
(closer to how talk actually flows) and over within-snippet topic-splitting (deferred:
snippets are short/VAD-gated and usually single-subject; manual re-tagging covers the
rare mixed one).

### ADR-028 — Dashboard: activity feed + manage pipeline-created Google items
**2026-06-04.** Two dashboard additions. (1) **Activity feed** (`/activity`, header 🔔
badge) — "what's new since you last checked": conversations processed, action items found
(with where each was routed — Calendar / Task / digest), and newly heard people. A `meta`
key/value table holds `activity_seen_at`; the badge counts new transcripts + intents since
then and resets on view. (2) **Manage synced items** — each intent records its
`calendar_event_id` / `calendar_link` / `gtask_id`, so the Tasks page shows where it landed
and links to the Calendar event; **dismissing a task also deletes its Google event/task**
(`google_sync.remove_intent`) — one place to catch and remove a wrong entry. Editing is via
the Google link (Calendar's own UI), not re-implemented here.

### ADR-027 — Drop Tasker & Flutter; notifications via Google + email
**2026-06-04.** No phone-side notification app. Reminders are delivered by **Google
Calendar/Tasks** (ADR-026) and the nightly **email digest** (ADR-024) — both already
surfaced by Gemini's daily review across the user's devices — so ADR-005's Tasker
recipes and the (deferred) Flutter app are unnecessary. Phone involvement is now just
the ESP32 captive-portal for WiFi provisioning + the tailnet dashboard (PWA).
**Supersedes the Tasker/Flutter portion of ADR-005.** Separately, ADR-021's 30-day
audio purge is now implemented (`purge.py`; daily 3 AM scheduler job — audio deleted,
transcripts/profiles kept).

### ADR-026 — Intent routing: Google Calendar/Tasks for reminders, email = digest
**2026-06-04.** Time-based reminders move to **Google** (Calendar + Tasks) instead of
us emailing at a set time: Google fires the reminder across all devices, the homelab
needn't be awake at reminder time, and Gemini's daily review reads Calendar + Tasks
natively. The LLM tags each intent `kind`; **semantic routing** (`google_sync.py`):
`event`→**Calendar event** (exact time + popup reminder), `task`→**Google Task** (date
due — the Tasks API discards time-of-day), `followup`→**email digest** only. Every dated
item also appears as a heads-up in the nightly digest; per-action timed emails are
**dropped** (complements ADR-024, whose nightly brief becomes digest-only). Auth is
**OAuth 2.0** (Calendar+Tasks scopes) with a stored refresh token — the Gmail App
Password (ADR-024) is **SMTP-only** and does NOT reach these APIs; consumer Gmail can't
use a service account for Tasks, so it's user-OAuth (consent screen must be **Published**
or the refresh token dies in 7 days). Intents carry `calendar_event_id`/`gtask_id`/
`synced_at` so re-processing never re-pushes the *same row*; the worker pushes after
profiling and no-ops safely until connected. **Semantic dedup** runs at *insert* time
(`intents.py`): a new intent is dropped if an open one with a similar action
(difflib ≥0.82) falls on the same local day — so repeated mentions and references to
the same event across different conversations collapse to a single event/task.

### ADR-025 — End-to-end pipeline worker + GPU gate
**2026-06-04.** `/ingest` is a **queue handoff**, not synchronous processing: it
stores the chunk (`transcribed=0`) and ACKs instantly, and a background **worker**
(`worker.py`) drains the queue end-to-end — ffmpeg-normalize → `wordattribute`
(WhisperX + diarize + ID) → `intents` → `profiles` → mark done — one chunk at a time.
Chunks persist until processed, so a PC that was **off or gaming self-heals** its
backlog on the next clear window. The **GPU gate** (`gpu_gate.py`, implements
ADR-015) reads the Windows `nvidia-smi.exe` over WSL interop and defers when a game
is running. **Refines ADR-015's threshold:** `util>40%` stays the primary "is a game
rendering" signal, but the free-VRAM floor is lowered **6 GB → 3 GB** — this box's
baseline (Windows desktop + the resident Ollama model the pipeline itself needs,
≈5–6 GB) already leaves <6 GB free with no game, so a 6 GB floor would defer forever.
The worker is managed like the MCP server (autostart + Settings restart/stop, so it's
revivable remotely). Default ASR model = **large-v3**. Implements H2/H3. (Also fixed:
renamed `profile.py`→`profiles.py` — it shadowed Python's stdlib `profile`, breaking
`transformers`/`torch` imports in the CUDA venvs; pinned `transformers==4.48.0` since
4.57 broke whisperx's import path.)

### ADR-024 — Email transport: local SMTP + Gmail App Password; nightly brief 23:50
**2026-06-04.** Outbound email is a **local `smtplib` SSL client** to
`smtp.gmail.com:465` authenticated with a **Google App Password** — not a
third-party email API/SaaS. Credentials live in `~/.listener.env` (WSL home,
`chmod 600`, gitignored, never on the Windows drive or in git); `listener.sh`
sources them into the always-on app. A **nightly "daily brief"** of open tasks is
sent at **23:50 America/Chicago** via APScheduler (ADR-006) inside the FastAPI app,
timed just before midnight so the next-morning Google Daily Brief captures it.
Keeps privacy-first (ADR-016): outbound-only authenticated SMTP, no inbound
exposure, no SaaS dependency; app passwords are scoped + revocable; `smtplib` is
stdlib (only new dep: apscheduler). Closes Q-S2.

### ADR-023 — Continuously-enriched speaker profiles + privacy delete
**2026-06-04.** Wires up the long-planned per-person profiles (ADR-014): after each
transcript a **local-LLM pass MERGES** new info into each named speaker's dossier
(`profile.py`). The profile is a true *personality/relational* picture — summary,
**traits**, interests, dislikes, **important dates**, notable facts (family/pets/
job) + a transient "lately" mood — explicitly **NOT** a list of their tasks (those
live in `intents`). The merge is **non-destructive**: durable fields evolve slowly
and additively while only the transient mood is overwritten, so one bad day never
rewrites who someone is. Relationship is a user-set **dropdown** including
**"Myself"** (the `speakers.is_self` device-owner flag, which frames everyone else's
relationship); the LLM only fills relationship when unset. Honors the per-speaker
**`do_not_profile`** opt-out. A **privacy delete** (`delete_speaker`, smart cascade)
removes a person's tasks, profile, voiceprint, and their lines in every transcript;
transcripts left empty (and their audio) are removed while **shared conversations
keep other speakers** — a per-person "right to be forgotten." Delete is **UI-only**
(confirm dialog), NOT an assistant tool; the assistant can only *read* profiles
(`get_speaker_profile`, by name or id). Closes Q-S5.

### ADR-022 — Word-level speaker attribution via WhisperX
**2026-06-03.** Segment-level merging mis-tagged words on Whisper segments that
straddle a speaker change. Fix = **word-level**: **WhisperX** (faster-whisper +
wav2vec2 forced alignment) yields tight per-word timestamps; each word is assigned
to its diarization turn, then regrouped → segments split exactly at boundaries (and
per-speaker embeddings get cleaner). WhisperX pins torch to **CUDA 12 (cu128)** so
it coexists with CTranslate2 in one venv (`~/listener-wx`). `wordattribute.py`
supersedes `attribute.py`. True simultaneous/overlapping speech stays the hard limit.

### ADR-021 — Retention: audio 30 days, transcripts indefinite
**2026-06-03.** Raw **audio kept 30 days** (enough to replay snippets for speaker
tagging/re-tagging), then auto-purged by a daily job. **Transcripts, segments,
embeddings, and profiles kept indefinitely** (text + derived vectors, far less
sensitive than raw audio). Snippet playback degrades to text-only once a chunk's
audio is gone. Resolves Q-S4. Privacy: local-only (ADR-016) + tailnet-only
(ADR-019) + `do_not_profile` (Q-S5).

### ADR-020 — Page assistant: dedicated MCP server + small local model
**2026-06-03.** The dashboard's conversational helper is a **dedicated MCP server**
exposing scoped DB tools (find_profile, edit_task, tag/merge_speaker, …) — no raw
SQL. Driven by a **small fast local model via Ollama** (separate from the heavy
intent-split model; shortlist Qwen3-4B / Llama-3.2-3B / Phi-4-mini / Gemma-3-4B,
benchmark). **Restartable from the dashboard Settings page** (revive it remotely if
WSL drops it). Fully local now; Gemini Flash deferred (security paramount).

### ADR-019 — Web app: FastAPI + HTMX, Tailscale Serve (+ Funnel for /ingest)
**2026-06-03.** One FastAPI app = `/ingest` (signed device upload) + HTMX/Jinja
dashboard (profiles, tasks, speaker-tagging, edit) + the assistant. Server-rendered
HTMX (no JS build). **Security: zero port-forwarding.** Dashboard via **Tailscale
Serve** (tailnet-only HTTPS, reach from phone anywhere, invisible to public).
**Only `/ingest`** is exposed publicly via **Tailscale Funnel**, HMAC+token+replay
locked. Tailscale ACLs = the allowlist. Reads/writes `listener.db`; snippets sliced
on-demand from stored audio.

### ADR-018 — Upload cadence: batched bursts, radio off between
**2026-06-03.** Device records continuously (VAD) to NAND; the WiFi radio stays
**off** between uploads. Uploads in batched bursts: **default every ~15–30 min on
home WiFi, immediately on home-WiFi (re)connect, and early if the buffer exceeds
~8 MB.** Away (hotspot/Funnel) less often / opt-in; offline → keep buffering.
Uploads cost ~40 mAh/day (negligible — *listening* dominates battery). Ample lead
for SOON items (≤30 min + processing ≪ the typical 3–4 hr horizon). Config knob.

### ADR-017 — Time handling: LLM emits local, code resolves UTC via IANA zone
**2026-06-03.** The prompt gives the model the current local time + IANA tz
`America/Chicago`. The model returns a **local** time/phrase (`due_local`/`due_text`)
— never raw UTC arithmetic (LLMs are unreliable at offset/DST math). **Code** converts
to UTC via `zoneinfo`/`dateparser` (RELATIVE_BASE=now, PREFER_DATES_FROM=future); the
IANA zone resolves CST/CDT automatically so DST is never tracked by hand. **Store UTC**,
render Central. The model's `due_local` is kept as a cross-check.

### ADR-016 — Fully-local pipeline via Ollama; LLM model TBD from 12GB shortlist
**2026-06-03.** ALL processing stays on the homelab GPU — faster-whisper, pyannote,
ECAPA, and the LLM — for privacy: raw audio / transcripts / voice profiles never leave
the box (Gemini only ever sees the curated *outgoing email*). LLM served via **Ollama**
with grammar/JSON-constrained output. **Model not hard-committed** — benchmark a
12GB-class shortlist on real transcripts, pick by JSON validity + SOON/LATER accuracy:
**Phi-4 Reasoning 14B, Qwen3 14B, Mistral Small 3 7B, Gemma 3 12B**. (Kimi/MiniMax are
1T / hundreds-of-B MoE → off-hardware + API-only → excluded.) VRAM: run the audio stage
then the LLM stage (load/unload) to stay under 12 GB. Resolves Q-S3 → local; strengthens
Q-S5.

### ADR-015 — GPU-aware processing gate (auto-detect gaming), defer heavy work
**2026-06-03.** The homelab GPU is shared with gaming. The heavy lane checks a gate
**on-demand before each job** (not continuous polling): reads the **Windows
`nvidia-smi.exe`** via WSL interop (the Linux WSL `nvidia-smi` can't reliably see host
graphics apps). **Defer if free VRAM < ~6 GB OR avg GPU-util > ~40%**; when deferred,
sleep ~10–30 min and re-check (60 s averaged sample to confirm clear; hysteresis).
Gate is checked only when the worker is **idle**, so the pipeline never gates itself.
Backlog self-heals at the 3 AM idle window before the 6 AM summary. CPU work (ingest,
scheduler, email) never pauses → **timely emails always fire, even mid-game.** No fixed
quiet-hours cron needed.

### ADR-014 — Speaker diarization, identification & relational profiling
**2026-06-03.** Resolves issue #1. Every transcript is **speaker-attributed** and
the pipeline builds **per-person relational profiles**. Stack (homelab GPU):
**pyannote.audio** diarization + **SpeechBrain ECAPA-TDNN** 192-d voice embeddings
for cross-recording ID, aligned to faster-whisper word timestamps. Unknown voices
**auto-cluster**; the user labels a cluster once in the dashboard and future audio
auto-tags by name. Speaker-attributed text feeds the (now speaker-aware) LLM intent
split and accumulates profiles (topics, emotion, recurring asks, recency). **Built
into Phase 4 core.** Chose direct pyannote+ECAPA over WhisperX (more control) and
over vosk (GPU accuracy ≫ vosk's lighter CPU x-vectors). Opens Q-S5/Q-S6/Q-S7.

### ADR-013 — Firmware in Arduino (arduino-esp32), not ESP-IDF
**2026-06-03.** Chosen for faster bring-up and the rich Arduino library ecosystem
(WiFiManager, HTTPClient, ESP_I2S). Uses arduino-esp32 core v3.x. Trade-off: a bit
less low-level control than IDF, acceptable here. W25N01 NAND needs a custom/adapted
SPI driver (no clean Arduino lib). Closes Q-F1. Encoding (Opus vs ADPCM) still Q-F2.

### ADR-012 — INMP441 as a pre-made module on a 2×(1×3) header
**2026-06-02.** Mount the INMP441 breakout module (Amazon 5-pack ~$12) on the main
board via **two 1×3 through-hole headers** at the module's measured row spacing
(2.54mm within each row), instead of placing a bare INMP441 MEMS chip. Rationale:
avoids MEMS reflow + acoustic-port assembly risk and JLCPCB stock uncertainty,
beginner-friendly, cheap; the module handles its own decoupling + acoustic port.
Deliberately relaxes the original "no breakout boards" guideline **for the mic
only**. Tradeoff: taller/bulkier + a hand-assembly step. Closes Q-H7, Q-H11.

### ADR-011 — Power path: TP4056 charging + P-MOSFET load-share
**2026-06-02.** Chosen over the Hub board's diode-OR (which never charged the cell)
and over plain TP4056 (load-sharing problem). USB-C charges the LiPo in place via
**TP4056 @ 1A** (Rprog 1.2k); system rail `VSYS` is fed from **VBUS through a 1N5819
Schottky (D5)** when plugged, and from the **battery through a P-MOSFET load-share
(Q1)** when unplugged. Q1 orientation **source=VSYS, drain=VBAT, gate=VBUS w/ 100k
pulldown** removes the battery-path diode drop (better runtime + LDO headroom) and
blocks back-feed. Detail in `docs/hardware/POWER-SECTION.md`. Supersedes the
power-path portion of ADR-008 (keeps 3000mAh + TP4056 + AP2112K). Closes Q-H9.

### ADR-010 — NAND on standard SPI (single-bit), not QSPI quad
**2026-06-02.** Reuses the proven Hub wiring: W25N01 on plain SPI with **WP#(IO2)
and HOLD#(IO3) pulled to 3V3 via 10k**, and a 10k pull-up on CS#. On-device Opus
encoding (ADR-002) makes the data rate tiny, so quad I/O is unnecessary; single SPI
is simpler to route and **frees GPIO9 and GPIO14**. Supersedes the QSPI routing in
the original hand-off and PINOUT. Optional future quad upgrade is sacrificed.

### ADR-009 — Homelab pipeline runs on WSL2 (Ubuntu)
**2026-06-02.** The i5/4070 server is Windows, but the Python pipeline (faster-
whisper/CTranslate2, APScheduler, FastAPI) runs far more smoothly on Linux, and
the user already has WSL2 set up with GitHub auth (BuoyAI workflow). CUDA works in
WSL2 via the Windows NVIDIA driver. Daemonize via WSL2 systemd units. Closes Q-S1.

### ADR-008 — Battery 3000mAh protected LiPo + dedicated charge IC (TP4056)
**2026-06-02.** Power budget ≈865 mAh/16h ×1.5 margin → ~1300 mAh/day minimum;
chose **3.7V 3000mAh protected LiPo, JST-PH 2.0** for ~1.5-day runtime in a
credit-card footprint. Charging requires a real CC/CV charge IC — **an LDO
(AP2112K) cannot charge a LiPo**. Use **TP4056 @ 1A** (Rprog 1.2k, ~0.33C, ~4h
overnight); the AP2112K stays as the 3.3V LDO; the on-hand 1N5819 Schottky does
USB↔battery power-path. Closes Q-H2.

### ADR-007 — Module confirmed: ESP32-S3-WROOM-1U-N16R8 (octal PSRAM)
**2026-06-02.** Confirmed N16R8 (16MB flash, 8MB **octal** PSRAM, LCSC C3013946,
qty 3). Octal PSRAM consumes **GPIO33–37** internally — they are reserved and must
not be used externally. The preliminary pinout already avoids them. Closes Q-H1.

### ADR-006 — Timed delivery via APScheduler, not sleeping subprocesses
**2026-06-02.** Time-sensitive actions ("email at 7 PM") are scheduled jobs, not
upload-latency problems. Use APScheduler with a SQLite job store so one-off and
recurring jobs survive reboots. Rejected raw `subprocess`/`sleep` (fragile, dies
on restart) and OS cron (one-off jobs awkward, less portable).

### ADR-005 — Minimal phone footprint
**2026-06-02.** No custom native app initially. ESP32 captive-portal handles
provisioning; homelab PWA handles review/edit; Tasker handles notifications.
Flutter deferred until live BLE status is actually wanted. Maximizes capability
per unit of effort.

### ADR-004 — Connectivity ladder; NAND is the offline buffer
**2026-06-02.** Device prefers home-WiFi LAN, falls back to hotspot/other WiFi
via a public ingress, and buffers to flash when fully offline. **Tailscale is
not run on the ESP32** (no viable port); the homelab exposes a single signed
`/ingest` path via Tailscale Funnel (or Cloudflare Tunnel). Preserves the user's
existing tailnet/Termius workflow.

### ADR-003 — VAD-gated recording with pre-roll + manual override
**2026-06-02.** Default voice-activated to save power/flash, but a continuous RAM
pre-roll ring buffer ensures quiet lead-ins aren't lost. Buttons force continuous
or hard-mute. Chosen over pure-continuous (battery/flash/privacy cost) and pure
push-to-talk (misses spontaneous moments).

### ADR-002 — Encode audio on-device
**2026-06-02.** 128 MB NAND can't hold raw 16 kHz audio (~1 hr). Encode to Opus
(~16 kbps) on-device, ADPCM as cheap fallback. faster-whisper decodes either.

### ADR-001 — EasyEDA Pro as the EDA tool
**2026-06-02.** User has prior EasyEDA Pro experience, orders from LCSC, and the
two reference boards already live there. Direct LCSC/JLCPCB integration minimizes
part-matching friction. KiCad rejected for relearn cost + manual LCSC mapping.

## Open Questions (close before the dependent phase)

### Hardware (block before Phase 2)
- *Resolved:* Q-H1 → ADR-007 (N16R8). Q-H2 → ADR-008 (3000mAh + TP4056).
  Q-H3 → ADR-008 (TP4056). Q-H4 → AP2112K-3.3 confirmed on hand (3x).
  Q-H6 → **void**: reference boards are from an unrelated old project, not reused.
- **Q-H5: Form factor / wearable enclosure** — pendant? clip? Credit-card-ish
  outline assumed. Affects mic port, button/LED placement, antenna keep-out.
- *Resolved:* Q-H7 + Q-H11 → ADR-012 (INMP441 breakout module on a 2×(1×3)
  header; measure row spacing with calipers; mic port faces the enclosure opening).
- *Resolved:* Q-H8 → ADR-011 (TP4056). Q-H9 → ADR-011 (P-MOSFET load-share).
  Q-H10 → **AO3401A** confirmed (G=1, S=2, D=3) for load-share Q1.

### Homelab (block before Phase 4)
- *Resolved:* Q-S1 → ADR-009 (WSL2/Ubuntu).
- *Resolved:* Q-S2 → ADR-024 (local SMTP + Gmail App Password; nightly brief 23:50).
- *Resolved:* Q-S3 → ADR-016 (fully local via Ollama; model TBD from 12GB shortlist).
- *Resolved:* Q-S4 → ADR-021 (audio 30 days, transcripts/embeddings/profiles indefinite).
- *Resolved:* Q-S5 → ADR-023 (`do_not_profile` opt-out + per-person privacy delete;
  profiles stay local per ADR-016; embedding/transcript retention per ADR-021).
- **Q-S6: Speaker-match threshold** — working default **0.40** (ECAPA cosine; same
  speaker ≈0.5+, different <0.3). Leave as-is; finalize against real multi-speaker
  field audio once the device is feeding live conversations.
- *Resolved:* Q-S7 → HF account + `pyannote/speaker-diarization-community-1` terms
  accepted, HF token configured (`hf auth login`). One-time setup complete.

### Firmware (block before Phase 3)
- *Resolved:* Q-F1 → ADR-013 (Arduino / arduino-esp32 v3.x).
- **Q-F2: Opus vs ADPCM** for v1 — start ADPCM/raw to get the pipeline working,
  add Opus later for compression. Depends on CPU/power headroom + PSRAM.
- *Resolved:* Q-F3 (upload cadence) → ADR-018 (batched bursts; ~15–30 min on home
  WiFi + on-reconnect + size cap; radio off between).
