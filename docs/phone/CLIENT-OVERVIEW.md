# Phone / Client Strategy

Goal: maximum capability for minimum build effort (ADR-005). No custom native app
at first.

## Three lightweight pieces
### 1. Device provisioning — ESP32 captive portal (no app)
- Hold MODE at boot → ESP32 hosts a SoftAP + web page.
- Set: home SSID/pass, hotspot SSID/pass, ingest URLs (LAN + Funnel), device
  token/secret. Saved to NVS.
- Works from any phone browser; nothing to install.

### 2. Context review/edit — homelab PWA
- Served by the homelab, reached over your **tailnet** (same VPN you already use
  with Termius). Installable to the home screen as a PWA.
- Views: recent transcripts, pending/scheduled intents (edit time / dismiss),
  long-term context + people profiles.
- **Conversational editor**: a chat box that drives an agent to edit the SQLite
  context ("merge these two people", "forget that note", "move trash to 8 PM").

### 3. Notifications — Google Calendar / Tasks + email (ADR-027)
- The homelab pushes dated reminders to **Google Calendar (events) / Tasks (to-dos)**
  (ADR-026) and sends a nightly **email digest** of undated follow-ups (ADR-024).
- Google fires the reminders across all your devices and Gemini's daily review
  surfaces them — so **no Tasker recipe and no phone app are needed**.

## Dropped (ADR-027): Tasker + Flutter
- Tasker is unnecessary now that Google handles delivery. The Flutter app (live BLE
  status / push-to-talk) is dropped too — revisit only if live BLE status is ever
  wanted. Phone involvement = captive-portal provisioning + the tailnet PWA only.
