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
  **relational profiles** — continuously LLM-enriched per transcript (see ADR-023).
- All on the homelab GPU. ⚠️ Profiling people's voices is sensitive — handled by a
  per-speaker `do_not_profile` opt-out + per-person privacy delete (see ADR-023);
  retention per ADR-021.

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
- **Dated reminders** → pushed to **Google Calendar (events) / Tasks (to-dos)** so
  Google fires the reminder at the right time across devices, even if the homelab is
  asleep; Gemini reads them natively (see ADR-026).
- **Undated follow-ups** → the nightly **daily brief** email (23:50 local, captured by
  the next-morning Google Daily Brief; ADR-024). Dated items also appear as a heads-up.
- This decouples reminder delivery from our box being awake at reminder time.

### Phone: minimal (no app — ADR-027)
- ESP32 **captive-portal** for WiFi/hotspot/token provisioning (no app).
- Homelab-served **PWA** (reached over your tailnet) for context review/edit.
- Notifications come from **Google Calendar/Tasks + the email digest** (ADR-026/024) —
  no Tasker, no Flutter (ADR-027).

## Trust / security boundaries
- Device holds a per-device secret; every POST is HMAC-signed + timestamped
  (replay window) over TLS.
- Funnel exposes only `/ingest`; everything else (dashboard, SSH) stays on the
  tailnet.
- Audio at rest on the homelab is access-controlled; retention policy TBD
  (see open questions).
