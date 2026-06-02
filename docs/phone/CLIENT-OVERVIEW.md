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

### 3. Immediate notifications — Tasker
- Homelab posts a webhook (or sends a tagged email) for fired SOON actions;
  Tasker turns it into a phone notification / action.
- This is the "arrives timely" path on the phone side.

## Deferred: Flutter app (only if needed)
- Justified only if you want **live BLE** status (recording indicator, battery,
  push-to-talk from the phone) when away from the dashboard.
- Bridges BLE (control/status) → HTTP/gRPC to homelab. Larger build; revisit
  after v1 proves the pipeline.

## Open
- Confirm Tasker is acceptable, or whether you'd rather get notifications purely
  via email/Workspace (then no Tasker needed).
