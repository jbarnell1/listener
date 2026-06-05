// Listener device — telemetry (ADR-031)
// Periodically reports device health to the homelab: battery, WiFi/signal, IP,
// uptime, free heap, firmware. Tiny + HMAC-signed; sent more often than audio.
// Standalone test sketch — later this merges into the main firmware.
//
// Battery: production board has a ÷2 divider (R7=R8=220k) on VBAT -> GPIO7 (ADC1).
// This dev board has NO divider, so leave BATT_ENABLED 0 (reports "n/a"); set it 1
// on the real board.  No external libraries (ESP32 core v3.x).

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <time.h>
#include "mbedtls/md.h"

// ---------- CONFIG ----------
struct Net { const char* ssid; const char* pass; };
Net networks[] = {
  { "YOUR_HOME_WIFI",   "YOUR_HOME_PASSWORD" },
  { "YOUR_PHONE_HOTSPOT", "YOUR_HOTSPOT_PASSWORD" },
};
const char* INGEST_SECRET = "PASTE_64_HEX_SECRET_HERE";   // grep LISTENER_INGEST_SECRET ~/.listener.env
const char* TELEMETRY_URL = "https://jon-desktop.taildc59f0.ts.net:8443/telemetry";
const char* DEVICE_ID     = "listener-01";
const char* FW_VERSION    = "v0.1-proto";

const uint32_t TELEMETRY_EVERY_MS = 60000;     // 60s for testing; ~5 min in production

#define BATT_ENABLED 0          // 1 on the production board (has the ÷2 divider)
#define BATT_ADC_PIN 7          // GPIO7 = ADC1_CH6 (PINOUT.md)
#define BATT_DIVIDER 2.0f       // R7=R8=220k -> VBAT = SENSE * 2
// ----------------------------

static uint32_t seq = 1;

bool connectWiFi() {
  for (auto& n : networks) {
    if (strlen(n.ssid) == 0) continue;
    Serial.printf("WiFi: trying \"%s\" ...", n.ssid);
    WiFi.begin(n.ssid, n.pass);
    uint32_t t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < 12000) { delay(300); Serial.print("."); }
    Serial.println();
    if (WiFi.status() == WL_CONNECTED) {
      Serial.printf("WiFi: connected %s  IP=%s  RSSI=%d\n",
                    n.ssid, WiFi.localIP().toString().c_str(), WiFi.RSSI());
      return true;
    }
  }
  return false;
}

bool syncTime() {
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  time_t now = 0; uint32_t t0 = millis();
  while ((now = time(nullptr)) < 1700000000 && millis() - t0 < 15000) delay(300);
  return now >= 1700000000;
}

int batteryMv() {
#if BATT_ENABLED
  uint32_t sum = 0;
  for (int i = 0; i < 16; i++) { sum += analogReadMilliVolts(BATT_ADC_PIN); delay(2); }
  return (int)((sum / 16.0f) * BATT_DIVIDER);     // SENSE mV * 2 = VBAT mV
#else
  return 0;                                        // no divider on this board -> n/a
#endif
}

String hmacHex(const char* key, const String& ts, const uint8_t* body, size_t len) {
  uint8_t out[32];
  const mbedtls_md_info_t* info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  mbedtls_md_context_t ctx; mbedtls_md_init(&ctx); mbedtls_md_setup(&ctx, info, 1);
  mbedtls_md_hmac_starts(&ctx, (const uint8_t*)key, strlen(key));
  mbedtls_md_hmac_update(&ctx, (const uint8_t*)ts.c_str(), ts.length());
  mbedtls_md_hmac_update(&ctx, body, len);
  mbedtls_md_hmac_finish(&ctx, out); mbedtls_md_free(&ctx);
  char hex[65]; for (int i = 0; i < 32; i++) sprintf(hex + i * 2, "%02x", out[i]); hex[64] = 0;
  return String(hex);
}

void sendTelemetry() {
  if (WiFi.status() != WL_CONNECTED && !connectWiFi()) return;
  String body = "{";
  body += "\"device\":\"" + String(DEVICE_ID) + "\",";
  body += "\"battery_mv\":" + String(batteryMv()) + ",";
  body += "\"rssi\":" + String(WiFi.RSSI()) + ",";
  body += "\"ssid\":\"" + WiFi.SSID() + "\",";
  body += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
  body += "\"uptime_s\":" + String(millis() / 1000) + ",";
  body += "\"free_heap\":" + String(ESP.getFreeHeap()) + ",";
  body += "\"fw\":\"" + String(FW_VERSION) + "\",";
  body += "\"seq\":" + String(seq);
  body += "}";

  String ts = String((long)time(nullptr));
  String sig = hmacHex(INGEST_SECRET, ts, (const uint8_t*)body.c_str(), body.length());
  WiFiClientSecure client; client.setInsecure();
  HTTPClient http;
  if (!http.begin(client, TELEMETRY_URL)) { Serial.println("http.begin failed"); return; }
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Ts", ts); http.addHeader("X-Sig", sig);
  int code = http.POST((uint8_t*)body.c_str(), body.length());
  Serial.printf("telemetry seq=%u -> HTTP %d  %s\n", seq, code, body.c_str());
  if (code == 200) seq++;
  http.end();
}

void setup() {
  Serial.begin(115200); delay(1000);
  Serial.println("\n=== Listener telemetry ===");
  connectWiFi();
  syncTime();
  sendTelemetry();
}

void loop() {
  delay(TELEMETRY_EVERY_MS);
  sendTelemetry();
}
