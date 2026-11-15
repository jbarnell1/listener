// Copy this file to secrets.h in the same folder and fill it in.
// secrets.h is gitignored. Never commit it.
#pragma once

struct WifiNet { const char* ssid; const char* pass; };

// Tried in this order: home network first, phone hotspot second.
static WifiNet WIFI_NETS[] = {
  { "YOUR_HOME_WIFI",     "YOUR_HOME_PASSWORD" },
  { "YOUR_PHONE_HOTSPOT", "YOUR_HOTSPOT_PASSWORD" },
};

// Per-device key: dashboard > Settings > Device keys > Issue key (shown once).
static const char* DEVICE_KEY = "PASTE_64_HEX_DEVICE_KEY_HERE";

// Where the device reaches the server, without a trailing slash. On the home network this
// can be the server's address; away from home it has to be a public HTTPS address that
// forwards only /ingest and /telemetry (for example a Tailscale Funnel).
#define LISTENER_BASE_URL "https://listener.example.com"
