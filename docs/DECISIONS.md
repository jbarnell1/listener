# Decision Log (ADRs) & Open Questions

Short, dated records of *why* we chose something. Newest first. When a decision
is reversed, add a new entry rather than editing the old one.

## Decisions

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
- **Q-S2: Email transport** — Gmail API vs SMTP app-password? "Day Ahead" + timed
  emails. Confirm the Google Workspace account + intended tagging scheme.
- *Resolved:* Q-S3 → ADR-016 (fully local via Ollama; model TBD from 12GB shortlist).
- *Resolved:* Q-S4 → ADR-021 (audio 30 days, transcripts/embeddings/profiles indefinite).
- **Q-S5: Voice-profile consent & retention** (ADR-014) — third parties haven't
  opted in. Retention of embeddings, `do_not_profile` list, delete-a-person,
  local-only guarantee (voice data never leaves the homelab).
- **Q-S6: Speaker-match threshold** — cosine sim to call a voice "the same person"
  (~0.7–0.8) + min samples/turn-length before enrolling/auto-naming a cluster.
- **Q-S7: pyannote model access** — gated HuggingFace models; need an HF account +
  accept the model terms to download (one-time setup).

### Firmware (block before Phase 3)
- *Resolved:* Q-F1 → ADR-013 (Arduino / arduino-esp32 v3.x).
- **Q-F2: Opus vs ADPCM** for v1 — start ADPCM/raw to get the pipeline working,
  add Opus later for compression. Depends on CPU/power headroom + PSRAM.
- *Resolved:* Q-F3 (upload cadence) → ADR-018 (batched bursts; ~15–30 min on home
  WiFi + on-reconnect + size cap; radio off between).
