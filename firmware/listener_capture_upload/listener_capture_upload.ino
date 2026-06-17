// listener_capture_upload.ino — press REC to capture ~10 s of LIVE mic audio and upload
// it (HMAC-signed) to /ingest, flagged as a deliberate capture (X-Mark) so it lands as a
// high-confidence item. This is the real capture->pipeline loop on the production board:
// your voice -> mic -> WAV -> /ingest -> transcript/tasks on the homelab.
//
// Real WiFi creds + this board's per-device key live in secrets.h (GITIGNORED).
// Signs with the per-device key + sends X-Device, so the homelab verifies against this
// board's own revocable key (ADR-042) — not the shared secret. Reuses the proven
// WiFi/NTP/HMAC/upload path from listener_device_proto; adds I2S capture.
//
// Arduino IDE: ESP32S3 Dev Module, USB CDC On Boot=Enabled, Flash 16MB, PSRAM=OPI PSRAM.

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <time.h>
#include "mbedtls/md.h"
#include <ESP_I2S.h>
#include "secrets.h"                 // WIFI_NETS[], INGEST_SECRET

// ---- pins (production board) ----
#define MIC_BCLK 4
#define MIC_WS   5
#define MIC_SD   6
#define BTN_REC  1                   // active-low push-to-talk
#define LED_REC  15                  // lit while recording
#define LED_STAT 16                  // lit on a successful upload

const char* INGEST_URL = "https://jon-desktop.taildc59f0.ts.net:8443/ingest";
const char* DEVICE_ID  = "listener-01";       // for the optional per-device-key path

const uint32_t SAMPLE_RATE = 16000;
const uint32_t REC_SECONDS = 10;
const uint32_t NUM_SAMPLES = SAMPLE_RATE * REC_SECONDS;   // 160000
const size_t   WAV_BYTES   = 44 + NUM_SAMPLES * 2;        // ~320 KB (lives in PSRAM)

I2SClass I2S;
uint8_t* wav = nullptr;
uint32_t seq = 1;

bool connectWiFi() {
  for (auto& n : WIFI_NETS) {
    if (!n.ssid || !strlen(n.ssid)) continue;
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
  Serial.println("WiFi: no network reachable.");
  return false;
}

bool syncTime() {
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");      // UTC
  time_t now = 0; uint32_t t0 = millis();
  while ((now = time(nullptr)) < 1700000000 && millis() - t0 < 15000) delay(300);
  if (now < 1700000000) { Serial.println("NTP: FAILED (ingest will 401 on stale ts)"); return false; }
  Serial.printf("NTP: unix time = %ld\n", (long)now);
  return true;
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

void wavHeader(uint8_t* b, uint32_t dataBytes) {
  memcpy(b, "RIFF", 4);                 *(uint32_t*)(b + 4)  = 36 + dataBytes;
  memcpy(b + 8, "WAVEfmt ", 8);         *(uint32_t*)(b + 16) = 16;
  *(uint16_t*)(b + 20) = 1;             *(uint16_t*)(b + 22) = 1;          // PCM, mono
  *(uint32_t*)(b + 24) = SAMPLE_RATE;   *(uint32_t*)(b + 28) = SAMPLE_RATE * 2;
  *(uint16_t*)(b + 32) = 2;             *(uint16_t*)(b + 34) = 16;         // block align, bits
  memcpy(b + 36, "data", 4);            *(uint32_t*)(b + 40) = dataBytes;
}

bool captureWav() {
  digitalWrite(LED_REC, HIGH);
  int32_t junk[256];
  for (int i = 0; i < 8; i++) I2S.readBytes((char*)junk, sizeof(junk));   // flush stale DMA
  wavHeader(wav, NUM_SAMPLES * 2);
  int16_t* d = (int16_t*)(wav + 44);
  uint32_t got = 0; int32_t buf[256]; uint32_t t0 = millis();
  while (got < NUM_SAMPLES) {
    size_t n = I2S.readBytes((char*)buf, sizeof(buf));
    int cnt = n / 4;
    for (int i = 0; i < cnt && got < NUM_SAMPLES; i++) {
      int32_t v = buf[i] >> 14;            // INMP441 24-bit MSB-aligned -> ~speech level
      if (v > 32767) v = 32767; else if (v < -32768) v = -32768;
      d[got++] = (int16_t)v;
    }
    if (millis() - t0 > REC_SECONDS * 1000 + 4000) break;   // safety timeout
  }
  digitalWrite(LED_REC, LOW);
  Serial.printf("captured %u samples (%.1f s)\n", (unsigned)got, got / (float)SAMPLE_RATE);
  return got > 0;
}

void upload() {
  if (WiFi.status() != WL_CONNECTED && !connectWiFi()) return;
  String ts  = String((long)time(nullptr));
  String sig = hmacHex(DEVICE_KEY, ts, wav, WAV_BYTES);       // per-device key (ADR-042)
  WiFiClientSecure client; client.setInsecure();              // prototype: skip cert check
  HTTPClient http;
  if (!http.begin(client, INGEST_URL)) { Serial.println("http.begin failed"); return; }
  http.addHeader("Content-Type", "application/octet-stream");
  http.addHeader("X-Device", DEVICE_ID);                      // server verifies w/ this board's key
  http.addHeader("X-Ts",  ts);
  http.addHeader("X-Sig", sig);
  http.addHeader("X-Seq", String(seq));
  http.addHeader("X-Mark", "1");        // deliberate PTT capture -> bypass triage (ADR-038)
  int code = http.POST((uint8_t*)wav, WAV_BYTES);
  String resp = http.getString();
  Serial.printf("ingest seq=%u (%u bytes) -> HTTP %d: %s\n", seq, (unsigned)WAV_BYTES, code, resp.c_str());
  digitalWrite(LED_STAT, code == 200 ? HIGH : LOW);
  if (code == 200) seq++;
  http.end();
}

void setup() {
  Serial.begin(115200);
  pinMode(BTN_REC, INPUT_PULLUP);
  pinMode(LED_REC, OUTPUT); pinMode(LED_STAT, OUTPUT);
  unsigned long t0 = millis(); while (!Serial && millis() - t0 < 6000) delay(10); delay(200);
  Serial.println("\n=== Listener capture + upload ===");
  wav = (uint8_t*)ps_malloc(WAV_BYTES);
  if (!wav) Serial.println("PSRAM alloc FAILED — set Tools > PSRAM = OPI PSRAM");
  I2S.setPins(MIC_BCLK, MIC_WS, -1, MIC_SD, -1);
  if (!I2S.begin(I2S_MODE_STD, SAMPLE_RATE, I2S_DATA_BIT_WIDTH_32BIT,
                 I2S_SLOT_MODE_MONO, I2S_STD_SLOT_LEFT))
    Serial.println("I2S begin FAILED");
  connectWiFi();
  syncTime();
  Serial.println(">>> press REC to record 10 s and upload <<<");
}

void loop() {
  if (wav && digitalRead(BTN_REC) == LOW) {            // REC pressed (active-low)
    delay(30);
    if (digitalRead(BTN_REC) != LOW) return;           // debounce
    Serial.println("REC — recording 10 s...");
    if (captureWav()) upload();
    while (digitalRead(BTN_REC) == LOW) delay(10);     // wait for release
    Serial.println(">>> press REC to capture again <<<");
  }
}
