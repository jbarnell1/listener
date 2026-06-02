# Decision Log (ADRs) & Open Questions

Short, dated records of *why* we chose something. Newest first. When a decision
is reversed, add a new entry rather than editing the old one.

## Decisions

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
- **Q-H7: INMP441 mic — NOT on hand (qty 0).** On the LCSC shopping list; confirm
  buy qty (suggest 3). Blocks audio bring-up.
- **Q-H8: Charger IC form** — TP4056 (SOP-8, recommended) vs MCP73831 (SOT-23-5,
  smaller). Default TP4056 unless size-critical.
- **Q-H9: Power-path / load-sharing** — simple 1N5819 Schottky OR-ing vs proper
  load-share MOSFET vs power-path PMIC. Decide during power-section schematic.

### Homelab (block before Phase 4)
- *Resolved:* Q-S1 → ADR-009 (WSL2/Ubuntu).
- **Q-S2: Email transport** — Gmail API vs SMTP app-password? "Day Ahead" + timed
  emails. Confirm the Google Workspace account + intended tagging scheme.
- **Q-S3: LLM for intent split** — local model vs Gemini API? Privacy vs quality.
- **Q-S4: Audio retention policy** — keep raw audio how long after transcription?

### Firmware (block before Phase 3)
- **Q-F1: Arduino vs ESP-IDF?** ESP-IDF gives better I2S/PSRAM/power control;
  Arduino is faster to start. (Hand-off leaned ESP-IDF.)
- **Q-F2: Opus vs ADPCM** for v1 — depends on CPU/power headroom + PSRAM.
