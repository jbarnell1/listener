# Decision Log (ADRs) & Open Questions

Short, dated records of *why* we chose something. Newest first. When a decision
is reversed, add a new entry rather than editing the old one.

## Decisions

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
- **Q-S3: LLM for intent split** — local model vs Gemini API? Privacy vs quality.
- **Q-S4: Audio retention policy** — keep raw audio how long after transcription?
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
