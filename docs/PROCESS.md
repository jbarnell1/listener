# Process & Roadmap

We work in phases. Each phase has a clear deliverable and a "definition of done"
so we always know where we are. We do **not** start a phase until the previous
one's open questions are resolved enough to avoid rework.

## Phase 1 — Align & Scaffold  ⬅️ *current*
- [x] Agree on end-to-end architecture (see `ARCHITECTURE.md`)
- [x] Resolve the big forks: EDA tool, connectivity, recording mode, phone app
- [x] Init git + private GitHub repo
- [x] Write foundational docs (this set)
- [ ] User fills in `hardware/EXISTING-HARDWARE.md` from the workbench
- [ ] User exports the two EasyEDA reference boards into `hardware/reference/`
- **Done when:** docs reviewed and inventory confirmed.

## Phase 2 — Hardware Design (EasyEDA Pro → JLCPCB)  ✅ DONE (2026-06-03)
Schematic (3 sheets) → 87×65mm 2-layer PCB → DRC clean → Gerbers → **5 boards
ordered at JLCPCB**; LCSC + Amazon parts ordered. ~2 week lead time. See
`hardware/PCB-LAYOUT.md`, `BOM.md`.

## Phase 3 — Firmware (ESP32-S3, **Arduino** — ADR-013)  ⬅️ *next*
- I2S mic capture → pre-roll ring buffer → VAD gate → ADPCM(→Opus) encode
- Chunked writes to Winbond NAND (custom W25N01 SPI driver, wear-aware)
- Connectivity ladder (home WiFi → hotspot/Funnel) + signed HTTPS upload
- Captive-portal provisioning, button modes, LED states, battery monitor
- Milestones M1–M8 in `firmware/FIRMWARE-OVERVIEW.md`; M1–M4/M6/M7 prototype on
  the on-hand ESP32 DevKit V1 while boards ship.
- **Done when:** device records, buffers offline, and uploads on reconnect.

## Phase 4 — Homelab Pipeline (Python)
- Ingest endpoint (verifies HMAC) → object store of chunks
- faster-whisper transcription (CUDA / RTX 4070 Super)
- **Speaker diarization + ID + relational profiling** (pyannote + ECAPA) — core,
  ADR-014: every transcript speaker-attributed; auto-cluster + dashboard labeling
- Speaker-aware LLM intent split: SOON vs LATER → structured rows in SQLite
- APScheduler dispatch (timed emails) + nightly daily-brief email (ADR-024)
- Speaker/profile store (continuously LLM-enriched, ADR-023) + conversational MCP
  assistant (ADR-020) + PWA review dashboard
- Milestones H1–H6 in `homelab/PIPELINE.md` (all testable with a recorded WAV)
- **Done when:** a speaker-attributed utterance produces a correctly-timed email.

## Phase 5 — Phone/Provisioning polish
- Captive-portal flow refined; Tasker recipes for immediate actions
- (Optional, later) Flutter app for live BLE status

## Working agreement
- Every non-obvious choice gets logged in `DECISIONS.md`.
- Open questions live at the bottom of `DECISIONS.md`; we close them before the
  phase that depends on them.
