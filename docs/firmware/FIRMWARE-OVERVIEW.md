# Firmware Architecture (ESP32-S3)

Detailed code lands in Phase 3. This is the plan the code will follow.

## Framework
- Leaning **ESP-IDF** (better I2S/DMA, PSRAM, power, and the FSPI NAND driver
  control we need). Arduino-as-component possible if we want Arduino libs.
- Decision pending: **Q-F1**.

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
