# Architecture

End-to-end design of the Listener system. This is the source of truth; the
phase docs implement it.

```
                 ┌─────────────────────────────────────────┐
   WEARABLE      │  ESP32-S3-WROOM-1U                       │
   (PCB)         │   I2S mic ─▶ pre-roll ring buf ─▶ VAD    │
                 │     │                              │      │
                 │     ▼ (force-on / mute buttons)    ▼      │
                 │   Opus/ADPCM encode ─▶ chunked WAV/bin    │
                 │     │                                     │
                 │     ▼                                     │
                 │   Winbond W25N01 NAND (128MB) buffer      │
                 │     │                                     │
                 │     ▼  connectivity ladder                │
                 │   WiFi(home LAN) → WiFi(hotspot)→ Funnel  │
                 └─────────────────┬───────────────────────-┘
                                   │ signed HTTPS POST /ingest
                                   ▼
                 ┌─────────────────────────────────────────┐
   HOMELAB       │  i5 + RTX 4070 Super                      │
   (Python)      │   /ingest (verify HMAC) ─▶ chunk store    │
                 │     │                                     │
                 │     ▼                                     │
                 │   pyannote diarize + faster-whisper ─▶     │
                 │     speaker-attributed transcript         │
                 │     │                                     │
                 │     ▼                                     │
                 │   ECAPA embeddings ─▶ ID vs voice library │
                 │     ─▶ relational profiles (people)       │
                 │     │                                     │
                 │     ▼                                     │
                 │   LLM intent split (speaker-aware) ──┬─SOON│
                 │     │                └─ LATER (tomorrow+)  │
                 │     ▼                                     │
                 │   SQLite intents + speakers + profiles    │
                 │     │                                     │
                 │     ▼                                     │
                 │   APScheduler ─▶ timed email/webhook      │
                 │   daily 6AM "Day Ahead" summary           │
                 │   PWA dashboard + conversational editor   │
                 └─────────────────────────────────────────-┘
                                   │
                                   ▼  email (Gemini-tagged) / Tasker webhook
                                 PHONE
```

## Key design decisions (rationale in DECISIONS.md)

### Recording: VAD-gated with pre-roll + manual override
- Default = voice-activated. A **2–5 s RAM pre-roll ring buffer** is always
  running; when VAD trips we flush the pre-roll *first* so the quiet lead-in is
  never lost.
- Buttons: **Force-Continuous** ("don't miss anything") and **Privacy-Mute**.
- Skips idle/keyboard-noise periods → saves power and flash.

### Speaker diarization, identification & relational profiling (see ADR-014)
- Every transcript is **speaker-attributed**: pyannote.audio diarizes turns,
  faster-whisper transcribes, words align to turns.
- **ECAPA-TDNN voice embeddings** identify recurring people across recordings vs a
  **known-voice library**. Unknown voices auto-cluster; you label a cluster once in
  the dashboard ("this voice = wife") and future audio auto-tags.
- Speaker-attributed text makes intents attributable ("**wife** asked X") and builds
  **relational profiles** (topics, emotional tone, recurring asks, recency).
- All on the homelab GPU. ⚠️ Profiling people's voices is sensitive — consent &
  retention policy is an open question (Q-S5).

### Storage: encode on-device (can't store raw)
- 16 kHz/16-bit raw ≈ 115 MB/hr → 128 MB holds ~1 hr. Not viable.
- **Opus ~16 kbps ≈ 7 MB/hr (~17 hrs)** preferred; **ADPCM 4:1** is the cheap
  fallback. faster-whisper decodes Opus via ffmpeg.

### Connectivity: configurable priority ladder; flash is the offline buffer
1. **Home WiFi** → POST direct to homelab **LAN IP** (fast, no internet).
2. **Other WiFi / phone hotspot** → POST to **public ingress** (Tailscale
   Funnel or Cloudflare Tunnel), TLS + bearer token + HMAC.
3. **No network** → keep caching to NAND; upload opportunistically on reconnect.
- **Tailscale does NOT run on the ESP32.** The *homelab* runs Tailscale; the
  device speaks plain signed HTTPS. Existing phone VPN/Termius flow is unaffected
  because Funnel only exposes the single `/ingest` path.
- **BLE** = control/status/provisioning only, never bulk audio.

### Timeliness = scheduling, not connectivity
- LLM extracts structured intent `{action, due_time, tier}`.
- **Tier SOON** → APScheduler one-off job fires the email/notification at the
  right time (e.g., "trash tonight" → 7 PM), surviving reboots via SQLite job
  store.
- **Tier LATER** → folded into the daily "Day Ahead" morning email.
- This decouples low-latency delivery from always-on connectivity.

### Phone: minimal now, extensible later
- ESP32 **captive-portal** for WiFi/hotspot/token provisioning (no app).
- Homelab-served **PWA** (reached over your tailnet) for context review/edit.
- **Tasker** for immediate-action notifications.
- Optional **Flutter** app later for live BLE status.

## Trust / security boundaries
- Device holds a per-device secret; every POST is HMAC-signed + timestamped
  (replay window) over TLS.
- Funnel exposes only `/ingest`; everything else (dashboard, SSH) stays on the
  tailnet.
- Audio at rest on the homelab is access-controlled; retention policy TBD
  (see open questions).
