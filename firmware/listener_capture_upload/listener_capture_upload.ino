// listener_capture_upload.ino — Listener wearable firmware (first field-test build).
//
//   • Press REC  -> capture ~10 s of LIVE mic audio -> signed upload to /ingest (X-Mark
//     = deliberate capture, bypasses triage).
//   • Every few minutes -> report health to /telemetry (battery, WiFi, uptime) so the
//     device shows on the dashboard Device card while it runs untethered.
//   • Robust WiFi roam/reconnect. Headless-capable — does NOT block on a serial monitor,
//     so it runs fine on battery with USB unplugged.
//
// Auth: per-device key + X-Device (ADR-042) for BOTH endpoints. Real WiFi creds + the
// per-device key live in secrets.h (GITIGNORED) in this folder.
//
// Arduino IDE: ESP32S3 Dev Module · USB CDC On Boot=Enabled · Flash 16MB · PSRAM=OPI PSRAM.
// NOTE: not power-optimized yet (WiFi stays on, no light sleep) — that's the next firmware
// pass (#10). This build is for a first live/battery functional test.
//
// LEDs:  REC  = on while recording, triple-blink on a successful upload.
//        STAT = heartbeat (brief blink ~every 3 s) so you know it's alive on battery.

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <time.h>
#include "mbedtls/md.h"
#include <ESP_I2S.h>
#include "secrets.h"                  // WIFI_NETS[], DEVICE_KEY

// ---- identity / endpoints ----
const char* DEVICE_ID     = "listener-01";
const char* FW_VERSION    = "v0.2-field";
const char* INGEST_URL    = "https://jon-desktop.taildc59f0.ts.net:8443/ingest";
const char* TELEMETRY_URL = "https://jon-desktop.taildc59f0.ts.net:8443/telemetry";

// ---- pins (production board) ----
#define MIC_BCLK 4
#define MIC_WS   5
#define MIC_SD   6
#define BTN_REC  1                    // push-to-talk, active-low
#define BTN_MODE 2                    // reserved (VAD/continuous/mute — future)
#define LED_REC  15
#define LED_STAT 16
#define BATT_ADC 7                    // ADC1, behind the /2 divider
#define BATT_DIV 2.0f

// ---- capture / cadence ----
const uint32_t SAMPLE_RATE = 16000;
const uint32_t REC_SECONDS = 10;
const uint32_t NUM_SAMPLES = SAMPLE_RATE * REC_SECONDS;     // 160000
const size_t   WAV_BYTES   = 44 + NUM_SAMPLES * 2;          // ~320 KB (PSRAM)
const uint32_t TELEMETRY_EVERY_MS = 120000;                 // 2 min during the test

I2SClass I2S;
uint8_t* wav = nullptr;
uint32_t seq = 1;
uint32_t lastTelemetry = 0;
uint32_t lastHeartbeat = 0;

bool connectWiFi() {
  for (auto& n : WIFI_NETS) {
    if (!n.ssid || !strlen(n.ssid)) continue;
    Serial.printf("WiFi: trying \"%s\" ...", n.ssid);
    WiFi.begin(n.ssid, n.pass);
    uint32_t t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < 12000) { delay(300); Serial.print("."); }
    Serial.println();
    if (WiFi.status() == WL_CONNECTED) {
      Serial.printf("WiFi: %s  IP=%s  RSSI=%d\n",
                    n.ssid, WiFi.localIP().toString().c_str(), WiFi.RSSI());
      return true;
    }
  }
  Serial.println("WiFi: none reachable.");
  return false;
}
bool ensureWiFi() { return WiFi.status() == WL_CONNECTED || connectWiFi(); }

bool syncTime() {
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");        // UTC
  time_t now = 0; uint32_t t0 = millis();
  while ((now = time(nullptr)) < 1700000000 && millis() - t0 < 15000) delay(300);
  if (now < 1700000000) { Serial.println("NTP: FAILED"); return false; }
  Serial.printf("NTP: %ld\n", (long)now);
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

int batteryMv() {
  uint32_t sum = 0;
  for (int i = 0; i < 16; i++) { sum += analogReadMilliVolts(BATT_ADC); delay(2); }
  return (int)((sum / 16.0f) * BATT_DIV);
}

// One signed POST (per-device key + X-Device). audio=true adds X-Seq + X-Mark.
int signedPost(const char* url, const uint8_t* body, size_t len, const char* ctype, bool audio) {
  if (!ensureWiFi()) return -1;
  String ts  = String((long)time(nullptr));
  String sig = hmacHex(DEVICE_KEY, ts, body, len);
  WiFiClientSecure client; client.setInsecure();            // prototype: skip cert check
  HTTPClient http;
  if (!http.begin(client, url)) return -2;
  http.addHeader("Content-Type", ctype);
  http.addHeader("X-Device", DEVICE_ID);
  http.addHeader("X-Ts",  ts);
  http.addHeader("X-Sig", sig);
  if (audio) { http.addHeader("X-Seq", String(seq)); http.addHeader("X-Mark", "1"); }
  int code = http.POST((uint8_t*)body, len);
  http.end();
  return code;
}

void wavHeader(uint8_t* b, uint32_t dataBytes) {
  memcpy(b, "RIFF", 4);                 *(uint32_t*)(b + 4)  = 36 + dataBytes;
  memcpy(b + 8, "WAVEfmt ", 8);         *(uint32_t*)(b + 16) = 16;
  *(uint16_t*)(b + 20) = 1;             *(uint16_t*)(b + 22) = 1;
  *(uint32_t*)(b + 24) = SAMPLE_RATE;   *(uint32_t*)(b + 28) = SAMPLE_RATE * 2;
  *(uint16_t*)(b + 32) = 2;             *(uint16_t*)(b + 34) = 16;
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
      int32_t v = buf[i] >> 14;                    // INMP441 24-bit MSB-aligned -> speech level
      if (v > 32767) v = 32767; else if (v < -32768) v = -32768;
      d[got++] = (int16_t)v;
    }
    if (millis() - t0 > REC_SECONDS * 1000 + 4000) break;
  }
  digitalWrite(LED_REC, LOW);
  Serial.printf("captured %u samples (%.1f s)\n", (unsigned)got, got / (float)SAMPLE_RATE);
  return got > 0;
}

void uploadAudio() {
  if (!captureWav()) return;
  int code = signedPost(INGEST_URL, wav, WAV_BYTES, "application/octet-stream", true);
  Serial.printf("ingest seq=%u (%uB) -> HTTP %d\n", seq, (unsigned)WAV_BYTES, code);
  if (code == 200) {
    seq++;
    for (int i = 0; i < 3; i++) { digitalWrite(LED_REC, HIGH); delay(80); digitalWrite(LED_REC, LOW); delay(80); }
  }
}

void sendTelemetry() {
  char body[256];
  int n = snprintf(body, sizeof(body),
    "{\"device\":\"%s\",\"battery_mv\":%d,\"rssi\":%d,\"ssid\":\"%s\",\"ip\":\"%s\","
    "\"uptime_s\":%lu,\"free_heap\":%u,\"fw\":\"%s\",\"seq\":%u}",
    DEVICE_ID, batteryMv(), (int)WiFi.RSSI(), WiFi.SSID().c_str(),
    WiFi.localIP().toString().c_str(), (unsigned long)(millis() / 1000),
    (unsigned)ESP.getFreeHeap(), FW_VERSION, seq);
  int code = signedPost(TELEMETRY_URL, (uint8_t*)body, n, "application/json", false);
  Serial.printf("telemetry -> HTTP %d\n", code);
  lastTelemetry = millis();
}

void setup() {
  Serial.begin(115200);
  pinMode(BTN_REC, INPUT_PULLUP); pinMode(BTN_MODE, INPUT_PULLUP);
  pinMode(LED_REC, OUTPUT); pinMode(LED_STAT, OUTPUT);
  unsigned long t0 = millis(); while (!Serial && millis() - t0 < 2500) delay(10);  // brief, non-blocking
  Serial.printf("\n=== Listener %s (%s) ===\n", DEVICE_ID, FW_VERSION);
  wav = (uint8_t*)ps_malloc(WAV_BYTES);
  if (!wav) Serial.println("PSRAM alloc FAILED — set Tools > PSRAM = OPI PSRAM");
  I2S.setPins(MIC_BCLK, MIC_WS, -1, MIC_SD, -1);
  if (!I2S.begin(I2S_MODE_STD, SAMPLE_RATE, I2S_DATA_BIT_WIDTH_32BIT,
                 I2S_SLOT_MODE_MONO, I2S_STD_SLOT_LEFT))
    Serial.println("I2S begin FAILED");
  if (connectWiFi()) syncTime();
  sendTelemetry();                                 // announce on the dashboard right away
  Serial.println(">>> press REC to capture + upload <<<");
}

void loop() {
  if (wav && digitalRead(BTN_REC) == LOW) {        // REC pressed
    delay(30);
    if (digitalRead(BTN_REC) != LOW) return;       // debounce
    Serial.println("REC — recording 10 s...");
    uploadAudio();
    while (digitalRead(BTN_REC) == LOW) delay(10);  // wait for release
  }
  if (millis() - lastTelemetry > TELEMETRY_EVERY_MS) sendTelemetry();
  if (millis() - lastHeartbeat > 3000) {            // "alive" blink (battery, no serial)
    lastHeartbeat = millis();
    digitalWrite(LED_STAT, HIGH); delay(8); digitalWrite(LED_STAT, LOW);
  }
  delay(20);
}
