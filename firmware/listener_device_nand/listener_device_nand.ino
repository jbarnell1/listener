// Listener device — real audio + W25N01 NAND round-trip  (ADR-010 / ADR-013)
// Flow: WiFi roam -> NTP -> init NAND -> store embedded WAV to NAND -> read back ->
//       verify -> HMAC-sign -> HTTPS POST the read-back copy to /ingest.
// If the NAND isn't detected, it uploads the audio straight from flash instead, so
// the audio test still works. Uploads ONCE on boot (no 60s spam).
//
// REQUIRES audio_data.h in this folder (provides AUDIO_DATA[] + AUDIO_LEN). Record a
// 16kHz/16-bit/mono WAV, tell me the filename, and I'll generate that header for you.
//
// No external libraries — all in the ESP32 Arduino core (v3.x). Board: ESP32S3 Dev
// Module, USB CDC On Boot: Enabled, PSRAM: OPI PSRAM, Flash: 16MB.

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <SPI.h>
#include <time.h>
#include "mbedtls/md.h"
#include "audio_data.h"            // const uint8_t AUDIO_DATA[]; const size_t AUDIO_LEN;

// ---------- CONFIG (fill in same WiFi + secret as your proto sketch) ----------
struct Net { const char* ssid; const char* pass; };
Net networks[] = {
  { "YOUR_HOME_WIFI",   "YOUR_HOME_PASSWORD" },
  { "YOUR_PHONE_HOTSPOT", "YOUR_HOTSPOT_PASSWORD" },
};
const char* INGEST_SECRET = "PASTE_64_HEX_SECRET_HERE";       // grep LISTENER_INGEST_SECRET ~/.listener.env
const char* INGEST_URL    = "https://jon-desktop.taildc59f0.ts.net:8443/ingest";

// W25N01 NAND wiring — from spaceteam_irl hub-schematic.md (the Hub reference board).
#define NAND_CS   10   // CS_NAND
#define NAND_SCK  2    // SCK / CLK
#define NAND_MISO 11   // DO  (flash -> ESP)
#define NAND_MOSI 1    // DI  (ESP -> flash)
// -----------------------------------------------------------------------------

static uint32_t seq = 1;
SPIClass nandSPI(FSPI);
SPISettings nandCfg(20000000, MSBFIRST, SPI_MODE0);
const uint16_t NAND_PAGE = 2048;       // bytes per page
const uint16_t PAGES_PER_BLOCK = 64;

// ---------------- W25N01GV minimal driver ----------------
static inline void csL(){ digitalWrite(NAND_CS, LOW); }
static inline void csH(){ digitalWrite(NAND_CS, HIGH); }

uint8_t nandStatus(uint8_t reg){
  nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0x0F); nandSPI.transfer(reg);
  uint8_t s = nandSPI.transfer(0x00);
  csH(); nandSPI.endTransaction();
  return s;
}
void nandWriteStatus(uint8_t reg, uint8_t val){
  nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0x1F); nandSPI.transfer(reg); nandSPI.transfer(val);
  csH(); nandSPI.endTransaction();
}
void nandWriteEnable(){
  nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0x06); csH(); nandSPI.endTransaction();
}
void nandWaitBusy(){ while (nandStatus(0xC0) & 0x01) delayMicroseconds(50); }  // SR-3 bit0 = BUSY
void nandReset(){
  nandSPI.beginTransaction(nandCfg); csL(); nandSPI.transfer(0xFF); csH(); nandSPI.endTransaction();
  delay(2); nandWaitBusy();
}
void nandReadId(uint8_t* id){
  nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0x9F); nandSPI.transfer(0x00);        // opcode + 1 dummy
  id[0]=nandSPI.transfer(0); id[1]=nandSPI.transfer(0); id[2]=nandSPI.transfer(0);
  csH(); nandSPI.endTransaction();
}
void nandEraseBlock(uint16_t page){
  nandWriteEnable();
  nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0xD8); nandSPI.transfer(0x00);        // dummy + 16-bit page addr
  nandSPI.transfer((page>>8)&0xFF); nandSPI.transfer(page&0xFF);
  csH(); nandSPI.endTransaction();
  nandWaitBusy();
}
void nandWritePage(uint16_t page, const uint8_t* data, uint16_t len){
  nandWriteEnable();
  nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0x02); nandSPI.transfer(0x00); nandSPI.transfer(0x00);  // load buffer @col 0
  for (uint16_t i=0;i<len;i++) nandSPI.transfer(data[i]);
  csH(); nandSPI.endTransaction();
  nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0x10); nandSPI.transfer(0x00);        // execute: dummy + page addr
  nandSPI.transfer((page>>8)&0xFF); nandSPI.transfer(page&0xFF);
  csH(); nandSPI.endTransaction();
  nandWaitBusy();
}
void nandReadPage(uint16_t page, uint8_t* data, uint16_t len){
  nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0x13); nandSPI.transfer(0x00);        // load page->buffer: dummy + page addr
  nandSPI.transfer((page>>8)&0xFF); nandSPI.transfer(page&0xFF);
  csH(); nandSPI.endTransaction();
  nandWaitBusy();
  nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0x03); nandSPI.transfer(0x00); nandSPI.transfer(0x00); nandSPI.transfer(0x00); // read buffer @col0 + 1 dummy
  for (uint16_t i=0;i<len;i++) data[i]=nandSPI.transfer(0x00);
  csH(); nandSPI.endTransaction();
}
bool nandInit(){
  pinMode(NAND_CS, OUTPUT); csH();
  nandSPI.begin(NAND_SCK, NAND_MISO, NAND_MOSI, NAND_CS);
  nandReset();
  uint8_t id[3]; nandReadId(id);
  Serial.printf("NAND JEDEC ID: %02X %02X %02X (expect EF AA 21 for W25N01GV)\n", id[0], id[1], id[2]);
  if (id[0] != 0xEF) { Serial.println("NAND: not detected — check wiring/pins."); return false; }
  nandWriteStatus(0xA0, 0x00);   // SR-1: clear block-protect bits (unlock all)
  return true;
}
void nandStore(const uint8_t* data, size_t len){
  uint16_t nPages  = (len + NAND_PAGE - 1) / NAND_PAGE;
  uint16_t nBlocks = (nPages + PAGES_PER_BLOCK - 1) / PAGES_PER_BLOCK;
  for (uint16_t b=0;b<nBlocks;b++) nandEraseBlock(b*PAGES_PER_BLOCK);
  for (uint16_t p=0;p<nPages;p++){
    size_t off = (size_t)p*NAND_PAGE, rem = len - off;
    nandWritePage(p, data+off, rem < NAND_PAGE ? (uint16_t)rem : NAND_PAGE);
  }
}
void nandLoad(uint8_t* out, size_t len){
  uint16_t nPages = (len + NAND_PAGE - 1) / NAND_PAGE;
  for (uint16_t p=0;p<nPages;p++){
    size_t off = (size_t)p*NAND_PAGE, rem = len - off;
    nandReadPage(p, out+off, rem < NAND_PAGE ? (uint16_t)rem : NAND_PAGE);
  }
}

// ---------------- comms (same scheme as the proto sketch) ----------------
bool connectWiFi(){
  for (auto& n : networks){
    if (strlen(n.ssid)==0) continue;
    Serial.printf("WiFi: trying \"%s\" ...", n.ssid);
    WiFi.begin(n.ssid, n.pass);
    uint32_t t0=millis();
    while (WiFi.status()!=WL_CONNECTED && millis()-t0<12000){ delay(300); Serial.print("."); }
    Serial.println();
    if (WiFi.status()==WL_CONNECTED){
      Serial.printf("WiFi: connected, IP=%s RSSI=%d\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
      return true;
    }
  }
  return false;
}
bool syncTime(){
  configTime(0,0,"pool.ntp.org","time.nist.gov");
  Serial.print("NTP: syncing"); time_t now=0; uint32_t t0=millis();
  while ((now=time(nullptr))<1700000000 && millis()-t0<15000){ delay(300); Serial.print("."); }
  Serial.println();
  if (now<1700000000){ Serial.println("NTP: FAILED"); return false; }
  Serial.printf("NTP: time=%ld\n",(long)now); return true;
}
String hmacHex(const char* key, const String& ts, const uint8_t* body, size_t len){
  uint8_t out[32];
  const mbedtls_md_info_t* info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  mbedtls_md_context_t ctx; mbedtls_md_init(&ctx); mbedtls_md_setup(&ctx, info, 1);
  mbedtls_md_hmac_starts(&ctx,(const uint8_t*)key,strlen(key));
  mbedtls_md_hmac_update(&ctx,(const uint8_t*)ts.c_str(),ts.length());
  mbedtls_md_hmac_update(&ctx,body,len);
  mbedtls_md_hmac_finish(&ctx,out); mbedtls_md_free(&ctx);
  char hex[65]; for(int i=0;i<32;i++) sprintf(hex+i*2,"%02x",out[i]); hex[64]=0;
  return String(hex);
}
void uploadChunk(const uint8_t* body, size_t len){
  if (WiFi.status()!=WL_CONNECTED && !connectWiFi()) return;
  String ts = String((long)time(nullptr));
  String sig = hmacHex(INGEST_SECRET, ts, body, len);
  WiFiClientSecure client; client.setInsecure();
  HTTPClient http;
  if (!http.begin(client, INGEST_URL)){ Serial.println("http.begin failed"); return; }
  http.addHeader("Content-Type","application/octet-stream");
  http.addHeader("X-Ts",ts); http.addHeader("X-Sig",sig); http.addHeader("X-Seq",String(seq));
  int code = http.POST((uint8_t*)body, len);
  Serial.printf("ingest seq=%u (%u bytes) -> HTTP %d: %s\n", seq,(unsigned)len,code,http.getString().c_str());
  if (code==200) seq++;
  http.end();
}

void setup(){
  Serial.begin(115200); delay(1200);
  Serial.println("\n=== Listener device — audio + NAND ===");
  Serial.printf("PSRAM: %s (%u bytes)\n", psramFound()?"found":"NONE", (unsigned)ESP.getPsramSize());
  Serial.printf("embedded audio: %u bytes\n", (unsigned)AUDIO_LEN);
  if (!connectWiFi()){ Serial.println("no WiFi; restart in 10s"); delay(10000); ESP.restart(); }
  syncTime();

  const uint8_t* toUpload = AUDIO_DATA; size_t len = AUDIO_LEN;
  if (nandInit()){
    Serial.println("NAND: storing audio -> reading back -> verifying...");
    nandStore(AUDIO_DATA, AUDIO_LEN);
    uint8_t* buf = (uint8_t*)ps_malloc(AUDIO_LEN);
    if (buf){
      nandLoad(buf, AUDIO_LEN);
      bool ok = (memcmp(buf, AUDIO_DATA, AUDIO_LEN)==0);
      Serial.printf("NAND round-trip verify: %s\n", ok ? "PASS (read matches written)" : "MISMATCH");
      toUpload = buf;                     // upload the copy that came back from NAND
    } else {
      Serial.println("ps_malloc failed (enable OPI PSRAM); uploading from flash.");
    }
  } else {
    Serial.println("NAND unavailable — uploading audio straight from flash.");
  }
  uploadChunk(toUpload, len);
  Serial.println("done. (idle — reset to send again)");
}

void loop(){ delay(30000); }   // one-shot; no spam
