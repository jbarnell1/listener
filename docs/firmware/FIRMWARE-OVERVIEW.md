# Firmware Architecture (ESP32-S3)

Detailed code lands in Phase 3. This is the plan the code will follow.

## Framework — Arduino (ADR-013)
- **arduino-esp32 core v3.x.** Board = ESP32S3 Dev Module, PSRAM = OPI, Flash = 16MB,
  USB CDC On Boot = Enabled.
- NAND is **standard SPI single-bit** (ADR-010) — custom/adapted W25N01 driver.

## Libraries
| Need | Library |
|------|---------|
| I2S mic | `ESP_I2S.h` (core 3.x) |
| WiFi + upload | `WiFi.h`, `WiFiClientSecure.h`, `HTTPClient.h` |
| Config/secrets in NVS | `Preferences.h` |
| Captive-portal provisioning | WiFiManager (tzapu) |
| HMAC-SHA256 signing | mbedTLS (bundled) |
| W25N01 NAND | custom thin SPI driver (page R/W, block erase, ECC, bad-block) |
| Encoding | ADPCM (DIY) for v1; Opus later (Q-F2) |

## Build-up milestones (each independently testable)
| # | Milestone | DevKit-testable? |
|---|-----------|------------------|
| M1 | Blink + serial + WiFi connect | ✅ |
| M2 | HTTP POST a test payload to homelab | ✅ |
| M3 | I2S capture from INMP441 → print RMS | ✅ (remap pins) |
| M4 | VAD gate + pre-roll ring buffer | ✅ |
| M5 | W25N01 driver: page R/W, append-log, ECC/bad-block | needs chip |
| M6 | Encode chunk (ADPCM) + WAV/bin framing | ✅ |
| M7 | Upload: signed POST, delete-on-ACK, offline retry | ✅ |
| M8 | UX: provisioning, buttons, LED states, battery ADC, light-sleep | partial |

> Prototype caveat: on-hand boards are **ESP32 DevKit V1 (classic ESP32)** — no
> native USB, usually no PSRAM, different GPIOs. Good for M1–M4/M6/M7 logic; the
> real S3 board is needed for M5 (NAND) + final M8.

## Task / data flow
```
[I2S DMA ISR] → fills DMA buffers @ 16kHz mono 16-bit
      │
      ▼
[Capture task] → copies into PRE-ROLL ring buffer (PSRAM, ~3-5s)
      │           runs VAD (energy + zero-crossing, hysteresis)
      │
      ├─ VAD idle  → keep overwriting pre-roll, write nothing to flash
      │
      └─ VAD active (or Force-Continuous) →
             flush pre-roll, then stream live frames to:
                 [Encoder task] → Opus(~16kbps) or ADPCM(4:1)
                       │
                       ▼
                 [Chunk writer] → fixed-size segments to W25N01 NAND
                       │           (header: ts, codec, len, seq, hmac-later)
                       ▼
                 [NAND log] (append-only, wear-aware, bad-block aware)
```

## Recording modes (MODE button cycles; LEDs show state)
| Mode | Behavior | LED |
|------|----------|-----|
| VAD (default) | record only on speech, with pre-roll | green slow blink |
| Force-Continuous | record everything | red solid |
| Privacy-Mute | mic ignored, nothing written | both off |

## Storage layout on NAND
- Treat NAND as an append-only segmented log (NOT a FAT filesystem at first):
  simpler, faster, wear-friendly. Each segment = one upload unit.
- Maintain bad-block table; W25N01 has on-die ECC — enable and check status.
- Index region tracks {segment, uploaded?, acked?} so we only delete after the
  homelab ACKs receipt.

## Connectivity ladder (uploader task)
1. Try saved **home SSID** → POST chunks to **LAN IP**.
2. Else try other known SSID / **phone hotspot** → POST to **Funnel URL**.
3. Else stay buffered; retry on WiFi event.
- Each POST: TLS, `Authorization: Bearer <token>`, `X-Sig: HMAC(secret, body+ts)`.
- Delete local segment only on `200 {acked: seq}`.

## Upload cadence (ADR-018)
- Recording is continuous (VAD); the **WiFi radio stays OFF between uploads** (the
  real battery saver). Uploads are **batched bursts**, triggered by:
  - **timer** — default every **~15–30 min** on home WiFi (configurable),
  - **(re)connect** to home WiFi — dump the buffer immediately, and
  - **size cap** — upload early if buffered audio > ~8 MB.
- Away (hotspot/Funnel): less often / opt-in. Offline: keep buffering to NAND.
- ~40 mAh/day for uploads (negligible); listening dominates battery.

## Provisioning
- First boot / hold MODE at boot → ESP32 starts SoftAP + captive portal to set:
  home SSID/pass, hotspot SSID/pass, ingest URLs, device token/secret.
- Stored in NVS.

## Power management
- Light-sleep between I2S DMA fills when idle; modem-sleep for WiFi.
- Battery sense on ADC1; low-batt LED + safe flush before brownout.
- Defensive decoupling on-board mitigates WiFi-tx brownouts (see BOM).

## Open
- Q-F1 (IDF vs Arduino), Q-F2 (Opus vs ADPCM for v1), VAD tuning, segment size.
