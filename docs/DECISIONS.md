# Decision Log (ADRs) & Open Questions

Short, dated records of *why* we chose something. Newest first. When a decision
is reversed, add a new entry rather than editing the old one.

## Decisions

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
- **Q-H1: Exact ESP32-S3 module variant?** e.g. `ESP32-S3-WROOM-1U-N16R8`
  (16 MB flash / 8 MB **octal** PSRAM) vs `-N8` / quad-PSRAM. Octal PSRAM
  consumes GPIO33–37, which changes the free-pin budget. **Need the exact part.**
- **Q-H2: Battery** — capacity (mAh), physical size, connector? Drives charge
  current (TP4056 Rprog) and board outline.
- **Q-H3: Charge controller exact part** — genuine TP4056 module vs bare IC vs
  a PMIC? Confirm against on-hand stock.
- **Q-H4: LDO choice** — AP2112K-3.3 (600 mA, better for WiFi tx spikes) vs
  MCP1700 (250 mA, lower Iq). WiFi bursts favor AP2112K. Confirm stock.
- **Q-H5: Form factor / wearable enclosure** — pendant? clip? Affects outline,
  mic port, button/LED placement, antenna keep-out.
- **Q-H6: Reference boards** — export both EasyEDA boards into
  `hardware/reference/` (schematic PDF + JSON or screenshots).

### Homelab (block before Phase 4)
- **Q-S1: Homelab OS** — Windows or Linux? Affects service mgmt, CUDA setup,
  Tailscale config, and how APScheduler is daemonized. (Dev box here is Win 11.)
- **Q-S2: Email transport** — Gmail API vs SMTP app-password? "Day Ahead" + timed
  emails. Confirm the Google Workspace account + intended tagging scheme.
- **Q-S3: LLM for intent split** — local model vs Gemini API? Privacy vs quality.
- **Q-S4: Audio retention policy** — keep raw audio how long after transcription?

### Firmware (block before Phase 3)
- **Q-F1: Arduino vs ESP-IDF?** ESP-IDF gives better I2S/PSRAM/power control;
  Arduino is faster to start. (Hand-off leaned ESP-IDF.)
- **Q-F2: Opus vs ADPCM** for v1 — depends on CPU/power headroom + PSRAM.
