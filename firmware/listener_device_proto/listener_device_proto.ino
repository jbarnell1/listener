// Listener device prototype — ESP32-S3 (ADR-013)
// Closes the device->pipeline loop: roam WiFi -> NTP -> generate a "mic-shaped" WAV
// -> HMAC-sign exactly like app.py's /ingest -> POST over HTTPS (Tailscale Funnel).
// No external libraries — everything here ships with the ESP32 Arduino core (v3.x).
//
// Fill in the 3 marked spots below (WiFi networks + ingest secret), then upload.
// Watch the Serial Monitor at 115200. A successful upload prints: HTTP 200 {"acked":N}

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <time.h>
#include "mbedtls/md.h"

// ---------- CONFIG ----------
struct Net { const char* ssid; const char* pass; };
Net networks[] = {                         // <<< 1) your WiFi networks (home + hotspot)
  { "YOUR_HOME_WIFI",   "YOUR_HOME_PASSWORD" },
  { "YOUR_PHONE_HOTSPOT", "YOUR_HOTSPOT_PASSWORD" },
};

// <<< 2) paste the secret: in WSL run  grep LISTENER_INGEST_SECRET ~/.listener.env
const char* INGEST_SECRET = "PASTE_64_HEX_SECRET_HERE";

// Already set for you (Tailscale Funnel, public, HMAC-locked):
const char* INGEST_URL = "https://jon-desktop.taildc59f0.ts.net:8443/ingest";

const uint32_t UPLOAD_EVERY_MS = 60000;    // re-upload every 60s for testing
// ----------------------------

const uint32_t SAMPLE_RATE = 16000;
const uint32_t NUM_SAMPLES = 16000;        // 1 second
static uint8_t  wavBuf[44 + NUM_SAMPLES * 2];
static size_t   wavLen = 0;
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
      Serial.printf("WiFi: connected to %s  IP=%s  RSSI=%d dBm\n",
                    n.ssid, WiFi.localIP().toString().c_str(), WiFi.RSSI());
      return true;
    }
  }
  Serial.println("WiFi: no network reachable.");
  return false;
}

bool syncTime() {
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");   // UTC
  Serial.print("NTP: syncing");
  time_t now = 0; uint32_t t0 = millis();
  while ((now = time(nullptr)) < 1700000000 && millis() - t0 < 15000) { delay(300); Serial.print("."); }
  Serial.println();
  if (now < 1700000000) { Serial.println("NTP: FAILED (timestamp will be rejected)"); return false; }
  Serial.printf("NTP: unix time = %ld\n", (long)now);
  return true;
}

size_t makeWav(uint8_t* b) {                 // 1s 16kHz 16-bit mono — a quiet 440Hz tone
  uint32_t dataBytes = NUM_SAMPLES * 2;
  memcpy(b, "RIFF", 4);
  *(uint32_t*)(b + 4) = 36 + dataBytes;
  memcpy(b + 8, "WAVEfmt ", 8);
  *(uint32_t*)(b + 16) = 16;
  *(uint16_t*)(b + 20) = 1;                  // PCM
  *(uint16_t*)(b + 22) = 1;                  // mono
  *(uint32_t*)(b + 24) = SAMPLE_RATE;
  *(uint32_t*)(b + 28) = SAMPLE_RATE * 2;    // byte rate
  *(uint16_t*)(b + 32) = 2;                  // block align
  *(uint16_t*)(b + 34) = 16;                 // bits
  memcpy(b + 36, "data", 4);
  *(uint32_t*)(b + 40) = dataBytes;
  int16_t* d = (int16_t*)(b + 44);
  for (uint32_t i = 0; i < NUM_SAMPLES; i++)
    d[i] = (int16_t)(3000.0f * sinf(2.0f * PI * 440.0f * i / SAMPLE_RATE));
  return 44 + dataBytes;
}

String hmacHex(const char* key, const String& ts, const uint8_t* body, size_t len) {
  uint8_t out[32];
  const mbedtls_md_info_t* info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  mbedtls_md_context_t ctx; mbedtls_md_init(&ctx);
  mbedtls_md_setup(&ctx, info, 1);                       // 1 = HMAC
  mbedtls_md_hmac_starts(&ctx, (const uint8_t*)key, strlen(key));
  mbedtls_md_hmac_update(&ctx, (const uint8_t*)ts.c_str(), ts.length());  // sign ts + body
  mbedtls_md_hmac_update(&ctx, body, len);
  mbedtls_md_hmac_finish(&ctx, out);
  mbedtls_md_free(&ctx);
  char hex[65];
  for (int i = 0; i < 32; i++) sprintf(hex + i * 2, "%02x", out[i]);
  hex[64] = 0;
  return String(hex);
}

void uploadChunk(const uint8_t* body, size_t len) {
  if (WiFi.status() != WL_CONNECTED && !connectWiFi()) return;
  String ts  = String((long)time(nullptr));
  String sig = hmacHex(INGEST_SECRET, ts, body, len);
  WiFiClientSecure client;
  client.setInsecure();                       // prototype: skip TLS cert check
  HTTPClient http;
  if (!http.begin(client, INGEST_URL)) { Serial.println("http.begin failed"); return; }
  http.addHeader("Content-Type", "application/octet-stream");
  http.addHeader("X-Ts",  ts);
  http.addHeader("X-Sig", sig);
  http.addHeader("X-Seq", String(seq));
  int code = http.POST((uint8_t*)body, len);
  String resp = http.getString();
  Serial.printf("ingest seq=%u (%u bytes) -> HTTP %d: %s\n", seq, (unsigned)len, code, resp.c_str());
  if (code == 200) seq++;
  http.end();
}

void setup() {
  Serial.begin(115200);
  delay(1200);
  Serial.println("\n=== Listener device prototype ===");
  if (!connectWiFi()) { Serial.println("Restarting in 10s..."); delay(10000); ESP.restart(); }
  syncTime();
  wavLen = makeWav(wavBuf);
  Serial.printf("generated %u-byte test WAV\n", (unsigned)wavLen);
  uploadChunk(wavBuf, wavLen);
}

void loop() {
  delay(UPLOAD_EVERY_MS);
  uploadChunk(wavBuf, wavLen);
}
