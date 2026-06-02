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

## Phase 2 — Hardware Design (EasyEDA Pro → JLCPCB)
1. Block diagram → finalized **pinout table** (`hardware/PINOUT.md`)
2. Schematic: power (USB-C + charge + LDO), MCU, NAND, mic, UI
3. Footprint + LCSC part matching against on-hand stock
4. PCB layout (placement, routing, ground pour, antenna keep-out)
5. DRC + fab/assembly export → JLCPCB order
- **Done when:** Gerbers + BOM + CPL exported and design-rule-checked.

## Phase 3 — Firmware (ESP32-S3)
- I2S mic capture → pre-roll ring buffer → VAD gate → Opus/ADPCM encode
- Chunked writes to Winbond NAND (wear-aware)
- Connectivity ladder (home WiFi → hotspot/Funnel) + signed HTTPS upload
- Captive-portal provisioning, button modes, LED states, battery monitor
- **Done when:** device records, buffers offline, and uploads on reconnect.

## Phase 4 — Homelab Pipeline (Python)
- Ingest endpoint (verifies HMAC) → object store of chunks
- faster-whisper transcription (CUDA / RTX 4070 Super)
- LLM intent split: SOON vs LATER → structured rows in SQLite
- APScheduler dispatch (timed emails) + daily "Day Ahead" summary
- Context store + conversational edit agent + PWA review dashboard
- **Done when:** an utterance produces a correctly-timed email end-to-end.

## Phase 5 — Phone/Provisioning polish
- Captive-portal flow refined; Tasker recipes for immediate actions
- (Optional, later) Flutter app for live BLE status

## Working agreement
- Every non-obvious choice gets logged in `DECISIONS.md`.
- Open questions live at the bottom of `DECISIONS.md`; we close them before the
  phase that depends on them.
