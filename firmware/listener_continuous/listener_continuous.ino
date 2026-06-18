// listener_continuous.ino — Listener all-day continuous-capture firmware (Stage 2).
//
// Pipeline on the device (ADR-003 / ADR-045 / ADR-018 / ADR-010):
//   I2S mic  ->  energy VAD + PSRAM pre-roll  ->  IMA ADPCM (4:1)  ->  NAND ring buffer
//   ->  batched signed upload to /ingest, WiFi radio OFF between bursts.
//
//   • Continuous: speech is detected automatically (no button). A ~1.5 s pre-roll ring
//     means word onsets aren't clipped. Silence closes a segment.
//   • REC button = force a *marked* capture right now (X-Mark, bypasses triage).
//   • MODE button = privacy MUTE toggle (stops capture; STAT LED double-blinks while muted).
//   • Periodic telemetry reports battery, WiFi, AND the NAND backlog + offline time so the
//     homelab can nudge you to turn on a hotspot before the buffer fills.
//
// Codec: IMA ADPCM in a WAV container (wFormatTag 0x0011) — ffmpeg/faster-whisper decode it
// natively. Set USE_ADPCM 0 to send plain PCM16 WAV instead (proven-decode path) while
// bench-testing VAD + ring, then flip to 1 and confirm the ADPCM segments transcribe.
//
// FIRST CUT — needs bench iteration (VAD threshold vs your room/voice; confirm ADPCM decode).
// Known V1 simplification: the pending-segment index is in RAM, so a hard reset/power loss
// drops audio not yet uploaded (persisting the index to NAND is a later refinement).
//
// Requires secrets.h (GITIGNORED) in THIS folder — copy it from listener_capture_upload:
//   struct WifiNet { const char* ssid; const char* pass; };
//   static WifiNet WIFI_NETS[] = {{"home","pw"},{"Pixel_9518","pw"}};
//   static const char* DEVICE_KEY = "…64 hex…";
//
// Arduino IDE: ESP32S3 Dev Module · USB CDC On Boot=Enabled · Flash 16MB · PSRAM=OPI PSRAM.

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <SPI.h>
#include <time.h>
#include "mbedtls/md.h"
#include <ESP_I2S.h>
#include "secrets.h"                    // WIFI_NETS[], DEVICE_KEY

// ---- identity / endpoints ----
const char* DEVICE_ID     = "listener-01";
const char* FW_VERSION    = "v0.4-power";
const char* INGEST_URL    = "https://jon-desktop.taildc59f0.ts.net:8443/ingest";
const char* TELEMETRY_URL = "https://jon-desktop.taildc59f0.ts.net:8443/telemetry";

// ---- pins (production board) ----
#define MIC_BCLK 4
#define MIC_WS   5
#define MIC_SD   6
#define BTN_REC  1                      // force marked capture, active-low
#define BTN_MODE 2                      // privacy mute toggle, active-low
#define LED_REC  15
#define LED_STAT 16
#define BATT_ADC 7
#define BATT_DIV 2.0f
// NAND (production, ADR-010/ADR-042)
#define NAND_CS  10
#define NAND_SCK 12
#define NAND_MISO 13
#define NAND_MOSI 11

// ---- capture / VAD / cadence (TUNE THESE on the bench) ----
#define USE_ADPCM        1              // 1 = IMA ADPCM 4:1 ; 0 = plain PCM16 (proven decode)
const uint32_t SAMPLE_RATE   = 16000;
const int      FRAME_SAMPLES = 320;     // 20 ms
const int      PREROLL_MS    = 2000;    // lead-in kept before voice onset
const int      VAD_HANG_MS   = 6000;    // bridge natural sentence pauses; pipeline stitches longer gaps (ADR-038)
const int      MIN_SEG_MS    = 500;     // discard blips shorter than this
const int      MAX_SEG_SEC   = 20;      // force-close long segments
const int32_t  VAD_RMS_ON    = 900;     // frame RMS to start (raise if it triggers on hiss)
const int32_t  VAD_RMS_OFF   = 700;     // frame RMS to keep alive (hysteresis)
const int      VAD_ON_FRAMES = 9;       // consecutive 20ms frames above ON to start (rejects clicks)
#define VAD_DEBUG        1              // 1 = LED_REC tracks segment-active + print peak rms ~1/s
const uint32_t BATCH_EVERY_MS   = 240000;   // try to drain the ring every 4 min (testing)
const uint32_t TELEMETRY_EVERY_MS = 120000; // 2 min (each wake powers the radio; raise to save battery)

// ---- power management (ADR-047) ----
#define POWER_RADIO_DUTY 1     // 1 = WiFi OFF between bursts (big battery win); on only to upload/report
#define POWER_CPU_SCALE  0     // 1 = drop CPU to 80MHz when idle, 240MHz online. OFF by default —
                               // verify OPI-PSRAM/I2S stay clean on your board before enabling.
const uint32_t CPU_IDLE_MHZ   = 80;
const uint32_t CPU_ONLINE_MHZ = 240;

const int   PREROLL_FRAMES = PREROLL_MS / 20;
const int   HANG_FRAMES    = VAD_HANG_MS / 20;
const int   MIN_SEG_FRAMES = MIN_SEG_MS / 20;
const int   MAX_SEG_SAMPLES = MAX_SEG_SEC * (int)SAMPLE_RATE;

// ================= NAND (W25N01) minimal driver — verified Stage 1 =================
SPIClass nandSPI(FSPI);
SPISettings nandCfg(20000000, MSBFIRST, SPI_MODE0);
const uint16_t NAND_PAGE = 2048;
const uint16_t PAGES_PER_BLOCK = 64;
const uint32_t RING_BLOCKS = 1024;                       // whole W25N01 (128 MB)
const uint32_t RING_PAGES  = RING_BLOCKS * PAGES_PER_BLOCK;

static inline void csL(){ digitalWrite(NAND_CS, LOW); }
static inline void csH(){ digitalWrite(NAND_CS, HIGH); }
uint8_t nandStatus(uint8_t reg){ nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0x0F); nandSPI.transfer(reg); uint8_t s=nandSPI.transfer(0x00);
  csH(); nandSPI.endTransaction(); return s; }
void nandWriteStatus(uint8_t reg,uint8_t val){ nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0x1F); nandSPI.transfer(reg); nandSPI.transfer(val);
  csH(); nandSPI.endTransaction(); }
void nandWriteEnable(){ nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0x06); csH(); nandSPI.endTransaction(); }
void nandWaitBusy(){ while (nandStatus(0xC0)&0x01) delayMicroseconds(50); }
void nandReset(){ nandSPI.beginTransaction(nandCfg); csL(); nandSPI.transfer(0xFF);
  csH(); nandSPI.endTransaction(); delay(2); nandWaitBusy(); }
void nandReadId(uint8_t* id){ nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0x9F); nandSPI.transfer(0x00);
  id[0]=nandSPI.transfer(0); id[1]=nandSPI.transfer(0); id[2]=nandSPI.transfer(0);
  csH(); nandSPI.endTransaction(); }
void nandEraseBlock(uint16_t page){ nandWriteEnable(); nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0xD8); nandSPI.transfer(0x00);
  nandSPI.transfer((page>>8)&0xFF); nandSPI.transfer(page&0xFF);
  csH(); nandSPI.endTransaction(); nandWaitBusy(); }
uint8_t nandWritePage(uint16_t page,const uint8_t* data,uint16_t len){ nandWriteEnable();
  nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0x02); nandSPI.transfer(0x00); nandSPI.transfer(0x00);
  for (uint16_t i=0;i<len;i++) nandSPI.transfer(data[i]);
  csH(); nandSPI.endTransaction();
  nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0x10); nandSPI.transfer(0x00);
  nandSPI.transfer((page>>8)&0xFF); nandSPI.transfer(page&0xFF);
  csH(); nandSPI.endTransaction(); nandWaitBusy(); return nandStatus(0xC0); }
void nandReadPage(uint16_t page,uint8_t* data,uint16_t len){ nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0x13); nandSPI.transfer(0x00);
  nandSPI.transfer((page>>8)&0xFF); nandSPI.transfer(page&0xFF);
  csH(); nandSPI.endTransaction(); nandWaitBusy();
  nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0x03); nandSPI.transfer(0x00); nandSPI.transfer(0x00); nandSPI.transfer(0x00);
  for (uint16_t i=0;i<len;i++) data[i]=nandSPI.transfer(0x00);
  csH(); nandSPI.endTransaction(); }
bool nandInit(){ pinMode(NAND_CS,OUTPUT); csH();
  nandSPI.begin(NAND_SCK,NAND_MISO,NAND_MOSI,NAND_CS); nandReset();
  uint8_t id[3]; nandReadId(id);
  Serial.printf("NAND ID %02X %02X %02X\n", id[0],id[1],id[2]);
  if (id[0]!=0xEF){ Serial.println("NAND missing!"); return false; }
  nandWriteStatus(0xA0,0x00); return true; }

// ---- ring buffer: forward-write pages, erase-before-block, RAM index of pending segments ----
struct Seg { uint32_t page; uint32_t bytes; uint32_t seq; bool marked; };
const int MAX_PENDING = 256;
Seg pending[MAX_PENDING];
int  pendCount = 0;
uint32_t writePage = 0;                 // next free page
uint32_t droppedSegs = 0;               // buffer-full drops (telemetry)

// erase the block that holds `page` only when we're at its first page (lazy erase)
void ringEnsureErased(uint32_t page){
  if (page % PAGES_PER_BLOCK == 0) nandEraseBlock((uint16_t)page);
}
// would writing `nPages` starting at writePage overwrite an un-uploaded segment? drop oldest if so.
void ringMakeRoom(uint32_t nPages){
  uint32_t endP = writePage + nPages;
  while (pendCount > 0){
    Seg& o = pending[0];
    uint32_t oEnd = o.page + (o.bytes + NAND_PAGE - 1)/NAND_PAGE;
    bool collide = (o.page < endP) && (oEnd > writePage);          // overlap in linear space
    if (writePage <= o.page && endP > o.page) collide = true;
    if (!collide) break;
    // drop oldest pending to make room
    for (int i=1;i<pendCount;i++) pending[i-1]=pending[i];
    pendCount--; droppedSegs++;
    Serial.println("ring FULL — dropped oldest unuploaded segment");
  }
}
// write a WAV blob to the ring; record it as pending. data is in PSRAM.
void ringWrite(const uint8_t* data, uint32_t bytes, uint32_t seqN, bool marked){
  uint32_t nPages = (bytes + NAND_PAGE - 1)/NAND_PAGE;
  if (nPages > RING_PAGES) return;
  if (writePage + nPages > RING_PAGES) writePage = 0;               // wrap
  ringMakeRoom(nPages);
  if (pendCount >= MAX_PENDING){                                    // index full: drop oldest
    for (int i=1;i<pendCount;i++) pending[i-1]=pending[i];
    pendCount--; droppedSegs++;
  }
  uint32_t startP = writePage;
  for (uint32_t i=0;i<nPages;i++){
    uint32_t pg = startP + i;
    ringEnsureErased(pg);
    uint32_t off = i*NAND_PAGE, rem = bytes - off;
    nandWritePage((uint16_t)pg, data+off, rem < NAND_PAGE ? (uint16_t)rem : NAND_PAGE);
  }
  writePage = startP + nPages;
  pending[pendCount++] = { startP, bytes, seqN, marked };
  Serial.printf("ring +seg seq=%u %uB @page %u (pending=%d)\n", seqN, bytes, startP, pendCount);
}
uint32_t ringBufferedBytes(){ uint32_t b=0; for (int i=0;i<pendCount;i++) b+=pending[i].bytes; return b; }

// ================= IMA ADPCM encoder (WAV, mono, blockAlign 256) =================
static const int8_t  IMA_IDX[16]={-1,-1,-1,-1,2,4,6,8,-1,-1,-1,-1,2,4,6,8};
static const int16_t IMA_STEP[89]={7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,
  50,55,60,66,73,80,88,97,107,118,130,143,157,173,190,209,230,253,279,307,337,371,408,449,
  494,544,598,658,724,796,876,963,1060,1166,1282,1411,1552,1707,1878,2066,2272,2499,2749,
  3024,3327,3660,4026,4428,4871,5358,5894,6484,7132,7845,8630,9493,10442,11487,12635,13899,
  15289,16818,18500,20350,22385,24623,27086,29794,32767};
const int BLOCK_ALIGN = 256;
const int SPB = 1 + (BLOCK_ALIGN-4)*2;  // samplesPerBlock = 505

static inline int imaEncode(int16_t s, int& pred, int& idx){
  int step = IMA_STEP[idx];
  int diff = s - pred, code = 0;
  if (diff < 0){ code = 8; diff = -diff; }
  if (diff >= step){ code |= 4; diff -= step; } step >>= 1;
  if (diff >= step){ code |= 2; diff -= step; } step >>= 1;
  if (diff >= step){ code |= 1; }
  int dq = IMA_STEP[idx] >> 3;
  if (code & 4) dq += IMA_STEP[idx];
  if (code & 2) dq += IMA_STEP[idx] >> 1;
  if (code & 1) dq += IMA_STEP[idx] >> 2;
  pred += (code & 8) ? -dq : dq;
  if (pred > 32767) pred = 32767; else if (pred < -32768) pred = -32768;
  idx += IMA_IDX[code]; if (idx < 0) idx = 0; else if (idx > 88) idx = 88;
  return code & 0x0F;
}

static inline void wrLE16(uint8_t* b,uint16_t v){ b[0]=v; b[1]=v>>8; }
static inline void wrLE32(uint8_t* b,uint32_t v){ b[0]=v; b[1]=v>>8; b[2]=v>>16; b[3]=v>>24; }

// Encode PCM (s, n samples) -> IMA-ADPCM WAV into out. Returns total byte length.
uint32_t encodeAdpcmWav(const int16_t* s, int n, uint8_t* out){
  int numBlocks = (n + SPB - 1)/SPB;
  uint32_t dataBytes = (uint32_t)numBlocks*BLOCK_ALIGN;
  uint32_t avgBps = (uint32_t)((float)SAMPLE_RATE/SPB*BLOCK_ALIGN);
  uint8_t* p = out;
  memcpy(p,"RIFF",4); wrLE32(p+4, 4+ (8+20) + (8+4) + (8+dataBytes)); memcpy(p+8,"WAVE",4); p+=12;
  memcpy(p,"fmt ",4); wrLE32(p+4,20); p+=8;
  wrLE16(p,0x0011); wrLE16(p+2,1); wrLE32(p+4,SAMPLE_RATE); wrLE32(p+8,avgBps);
  wrLE16(p+12,BLOCK_ALIGN); wrLE16(p+14,4); wrLE16(p+16,2); wrLE16(p+18,SPB); p+=20;
  memcpy(p,"fact",4); wrLE32(p+4,4); wrLE32(p+8,(uint32_t)n); p+=12;
  memcpy(p,"data",4); wrLE32(p+4,dataBytes); p+=8;
  int idx = 0;
  for (int b=0;b<numBlocks;b++){
    int base = b*SPB;
    int16_t first = s[base < n ? base : n-1];
    int pred = first;
    wrLE16(p, (uint16_t)first); p[2]=(uint8_t)idx; p[3]=0; p+=4;
    for (int k=1;k<SPB;k+=2){
      int i0 = base+k, i1 = base+k+1;
      int16_t a = (i0 < n) ? s[i0] : s[n-1];
      int16_t bb= (i1 < n) ? s[i1] : s[n-1];
      uint8_t lo = imaEncode(a,pred,idx);
      uint8_t hi = imaEncode(bb,pred,idx);
      *p++ = lo | (hi<<4);
    }
  }
  return (uint32_t)(p - out);
}

// Plain PCM16 WAV (USE_ADPCM 0) — the proven decode path for bench isolation.
uint32_t encodePcmWav(const int16_t* s, int n, uint8_t* out){
  uint32_t dataBytes = (uint32_t)n*2;
  memcpy(out,"RIFF",4); wrLE32(out+4,36+dataBytes); memcpy(out+8,"WAVEfmt ",8);
  wrLE32(out+16,16); wrLE16(out+20,1); wrLE16(out+22,1); wrLE32(out+24,SAMPLE_RATE);
  wrLE32(out+28,SAMPLE_RATE*2); wrLE16(out+32,2); wrLE16(out+34,16);
  memcpy(out+36,"data",4); wrLE32(out+40,dataBytes);
  memcpy(out+44, s, dataBytes);
  return 44+dataBytes;
}

// ================= buffers (PSRAM) =================
I2SClass I2S;
int16_t* preroll = nullptr;             // circular pre-roll, PREROLL_FRAMES*FRAME_SAMPLES
int      prerollHead = 0, prerollFill = 0;
int16_t* pcmSeg = nullptr;              // current segment PCM
int      segLen = 0;                    // samples in pcmSeg
uint8_t* wavOut = nullptr;              // encoded WAV scratch
const int PREROLL_CAP = PREROLL_FRAMES*FRAME_SAMPLES;
const size_t WAVOUT_CAP = (size_t)(MAX_SEG_SAMPLES+PREROLL_CAP)*2 + 4096;

uint32_t seq = 1;
bool muted = false;
bool inSeg = false;
bool markedSeg = false;                 // REC pressed -> mark the current/next segment
int  hangCount = 0;
uint32_t lastBatch = 0, lastTelemetry = 0, lastHeartbeat = 0;
uint32_t lastNetSeenMs = 0;             // for offline-duration telemetry

// ---- comms (per-device key, ADR-042) ----
bool connectWiFi(){
  static int lastGood = -1;
  int order[8]; int m=0;
  int total = sizeof(WIFI_NETS)/sizeof(WIFI_NETS[0]);
  if (lastGood >= 0 && lastGood < total) order[m++]=lastGood;     // try last-good first
  for (int i=0;i<total;i++) if (i!=lastGood) order[m++]=i;
  for (int j=0;j<m;j++){
    auto& n = WIFI_NETS[order[j]];
    if (!n.ssid || !strlen(n.ssid)) continue;
    WiFi.begin(n.ssid, n.pass);
    uint32_t t0=millis();
    while (WiFi.status()!=WL_CONNECTED && millis()-t0<10000) delay(250);
    if (WiFi.status()==WL_CONNECTED){ lastGood=order[j];
      Serial.printf("WiFi %s IP=%s RSSI=%d\n", n.ssid, WiFi.localIP().toString().c_str(), WiFi.RSSI());
      return true; }
  }
  return false;
}
String hmacHex(const char* key,const String& ts,const uint8_t* body,size_t len){
  uint8_t out[32]; const mbedtls_md_info_t* info=mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  mbedtls_md_context_t ctx; mbedtls_md_init(&ctx); mbedtls_md_setup(&ctx,info,1);
  mbedtls_md_hmac_starts(&ctx,(const uint8_t*)key,strlen(key));
  mbedtls_md_hmac_update(&ctx,(const uint8_t*)ts.c_str(),ts.length());
  mbedtls_md_hmac_update(&ctx,body,len); mbedtls_md_hmac_finish(&ctx,out); mbedtls_md_free(&ctx);
  char hex[65]; for (int i=0;i<32;i++) sprintf(hex+i*2,"%02x",out[i]); hex[64]=0; return String(hex);
}
int signedPost(const char* url,const uint8_t* body,size_t len,const char* ctype,bool audio,bool marked){
  String ts=String((long)time(nullptr));
  String sig=hmacHex(DEVICE_KEY,ts,body,len);
  WiFiClientSecure client; client.setInsecure();
  HTTPClient http; if (!http.begin(client,url)) return -2;
  http.addHeader("Content-Type",ctype); http.addHeader("X-Device",DEVICE_ID);
  http.addHeader("X-Ts",ts); http.addHeader("X-Sig",sig);
  if (audio){ http.addHeader("X-Seq",String(seq)); if (marked) http.addHeader("X-Mark","1"); }
  int code=http.POST((uint8_t*)body,len); http.end(); return code;
}
int batteryMv(){ uint32_t s=0; for (int i=0;i<16;i++){ s+=analogReadMilliVolts(BATT_ADC); delay(2);} return (int)((s/16.0f)*BATT_DIV); }

// ---- radio duty-cycling: WiFi is OFF except during an upload/report window (ADR-047) ----
bool wifiOn(){
  if (WiFi.status()==WL_CONNECTED) return true;
  WiFi.mode(WIFI_STA);
  if (POWER_CPU_SCALE) setCpuFrequencyMhz(CPU_ONLINE_MHZ);   // TLS/handshake wants the headroom
  bool ok = connectWiFi();
  if (!ok && POWER_CPU_SCALE) setCpuFrequencyMhz(CPU_IDLE_MHZ);
  return ok;
}
void wifiOff(){
  if (POWER_RADIO_DUTY){ WiFi.disconnect(true,true); WiFi.mode(WIFI_OFF); }
  if (POWER_CPU_SCALE) setCpuFrequencyMhz(CPU_IDLE_MHZ);
}

// ---- drain the NAND ring to /ingest; radio is already up (serviceNetwork owns on/off) ----
void drainRing(){
  if (pendCount == 0 || WiFi.status()!=WL_CONNECTED) return;
  lastNetSeenMs = millis();
  int uploaded=0;
  while (pendCount > 0){
    Seg s = pending[0];
    uint32_t nPages=(s.bytes+NAND_PAGE-1)/NAND_PAGE;
    for (uint32_t i=0;i<nPages;i++){
      uint32_t off=i*NAND_PAGE, rem=s.bytes-off;
      nandReadPage((uint16_t)(s.page+i), wavOut+off, rem<NAND_PAGE?(uint16_t)rem:NAND_PAGE);
    }
    int code=signedPost(INGEST_URL, wavOut, s.bytes, "application/octet-stream", true, s.marked);
    if (code==200){ seq++; uploaded++;
      for (int i=1;i<pendCount;i++) pending[i-1]=pending[i]; pendCount--;
      digitalWrite(LED_REC,HIGH); delay(40); digitalWrite(LED_REC,LOW);
    } else { Serial.printf("drain: seq upload HTTP %d — will retry next batch\n", code); break; }
  }
  Serial.printf("drain: uploaded %d, %d still pending\n", uploaded, pendCount);
  lastBatch=millis();
}

void sendTelemetry(){
  bool online = (WiFi.status()==WL_CONNECTED);
  uint32_t offlineS = online ? 0 : (millis()-lastNetSeenMs)/1000;
  char body[320];
  int n=snprintf(body,sizeof(body),
    "{\"device\":\"%s\",\"battery_mv\":%d,\"rssi\":%d,\"ssid\":\"%s\",\"uptime_s\":%lu,"
    "\"free_heap\":%u,\"fw\":\"%s\",\"buffered_segs\":%d,\"buffered_bytes\":%u,"
    "\"offline_s\":%u,\"dropped_segs\":%u,\"muted\":%d}",
    DEVICE_ID, batteryMv(), online?(int)WiFi.RSSI():0, online?WiFi.SSID().c_str():"",
    (unsigned long)(millis()/1000), (unsigned)ESP.getFreeHeap(), FW_VERSION,
    pendCount, ringBufferedBytes(), offlineS, droppedSegs, muted?1:0);
  if (!online){ lastTelemetry=millis(); return; }    // radio is owned by serviceNetwork()
  lastNetSeenMs=millis();
  int code=signedPost(TELEMETRY_URL,(uint8_t*)body,n,"application/json",false,false);
  Serial.printf("telemetry -> HTTP %d\n", code);
  lastTelemetry=millis();
}

// ---- one radio-on window: connect, drain the ring + report, radio back off ----
void serviceNetwork(){
  bool needDrain = pendCount>0 && (millis()-lastBatch>BATCH_EVERY_MS
                                   || ringBufferedBytes() > 8u*1024*1024);
  bool needTelem = millis()-lastTelemetry > TELEMETRY_EVERY_MS;
  if (!needDrain && !needTelem) return;
  if (!wifiOn()){                                    // no network — stay buffered, retry next interval
    Serial.println("net: no network — buffered on NAND");
    lastBatch=millis(); lastTelemetry=millis();      // back off so we don't hammer the radio
    wifiOff();
    return;
  }
  if (pendCount>0) drainRing();                       // drain everything while we're up
  sendTelemetry();
  wifiOff();
}

// ---- close the current segment: prepend pre-roll, encode, write to ring ----
void closeSegment(bool marked){
  if (VAD_DEBUG) digitalWrite(LED_REC,LOW);              // segment over -> LED off
  if (segLen < MIN_SEG_FRAMES*FRAME_SAMPLES){ segLen=0; inSeg=false; hangCount=0; return; }
  uint32_t bytes = USE_ADPCM ? encodeAdpcmWav(pcmSeg, segLen, wavOut)
                             : encodePcmWav(pcmSeg, segLen, wavOut);
  // copy out of wavOut before ringWrite reuses it? ringWrite reads wavOut directly into NAND — fine,
  // but drainRing also uses wavOut. We only drain during silence, never mid-close, so no overlap.
  ringWrite(wavOut, bytes, seq, marked);     // seq is a label; real seq increments on upload
  segLen=0; inSeg=false; hangCount=0;
}

// push one frame of PCM into the pre-roll ring (always)
void pushPreroll(const int16_t* f){
  for (int i=0;i<FRAME_SAMPLES;i++){
    preroll[(prerollHead+i)%PREROLL_CAP] = f[i];
  }
  prerollHead=(prerollHead+FRAME_SAMPLES)%PREROLL_CAP;
  if (prerollFill < PREROLL_CAP) prerollFill += FRAME_SAMPLES;
}
// when voice starts, seed the segment with the buffered pre-roll
void seedFromPreroll(){
  int start=(prerollHead - prerollFill + PREROLL_CAP*2)%PREROLL_CAP;
  for (int i=0;i<prerollFill && segLen<MAX_SEG_SAMPLES;i++)
    pcmSeg[segLen++]=preroll[(start+i)%PREROLL_CAP];
}
void appendSeg(const int16_t* f){
  for (int i=0;i<FRAME_SAMPLES && segLen<MAX_SEG_SAMPLES;i++) pcmSeg[segLen++]=f[i];
}

int32_t frameRms(const int16_t* f){
  int32_t mean=0; for (int i=0;i<FRAME_SAMPLES;i++) mean+=f[i];
  mean/=FRAME_SAMPLES;                                   // DC offset (INMP441 bias)
  int64_t acc=0; for (int i=0;i<FRAME_SAMPLES;i++){ int v=f[i]-mean; acc+=(int64_t)v*v; }
  return (int32_t)sqrt((double)(acc/FRAME_SAMPLES));     // AC energy only
}

void readFrame(int16_t* out){
  int32_t buf[FRAME_SAMPLES]; size_t need=FRAME_SAMPLES*4, got=0;
  while (got<need){ size_t n=I2S.readBytes((char*)buf+got, need-got); if (!n) break; got+=n; }
  int cnt=got/4;
  for (int i=0;i<FRAME_SAMPLES;i++){
    int32_t v = (i<cnt) ? (buf[i]>>14) : 0;
    if (v>32767) v=32767; else if (v<-32768) v=-32768;
    out[i]=(int16_t)v;
  }
}

void setup(){
  Serial.begin(115200);
  pinMode(BTN_REC,INPUT_PULLUP); pinMode(BTN_MODE,INPUT_PULLUP);
  pinMode(LED_REC,OUTPUT); pinMode(LED_STAT,OUTPUT);
  unsigned long t0=millis(); while (!Serial && millis()-t0<2500) delay(10);
  Serial.printf("\n=== Listener %s (%s) ===\n", DEVICE_ID, FW_VERSION);

  preroll=(int16_t*)ps_malloc(PREROLL_CAP*sizeof(int16_t));
  pcmSeg =(int16_t*)ps_malloc((size_t)MAX_SEG_SAMPLES*sizeof(int16_t));
  wavOut =(uint8_t*)ps_malloc(WAVOUT_CAP);
  if (!preroll||!pcmSeg||!wavOut){ Serial.println("PSRAM alloc FAILED — set PSRAM=OPI PSRAM"); }

  I2S.setPins(MIC_BCLK,MIC_WS,-1,MIC_SD,-1);
  if (!I2S.begin(I2S_MODE_STD,SAMPLE_RATE,I2S_DATA_BIT_WIDTH_32BIT,I2S_SLOT_MODE_MONO,I2S_STD_SLOT_LEFT))
    Serial.println("I2S begin FAILED");
  if (!nandInit()) Serial.println("WARNING: NAND down — segments cannot be stored");
  lastNetSeenMs=millis();
  WiFi.mode(WIFI_STA);
  if (connectWiFi()){ configTime(0,0,"pool.ntp.org","time.nist.gov");
    time_t now=0; uint32_t s=millis(); while ((now=time(nullptr))<1700000000 && millis()-s<15000) delay(300);
    sendTelemetry(); }                              // announce on the dashboard while we're up
  wifiOff();                                        // start low-power: radio off until the next window
  Serial.println(">>> continuous capture running. REC=mark now · MODE=mute <<<");
}

void loop(){
  // buttons
  if (digitalRead(BTN_MODE)==LOW){ delay(30); if (digitalRead(BTN_MODE)==LOW){
    muted=!muted; if (muted && inSeg) closeSegment(false);
    Serial.printf("MODE: %s\n", muted?"MUTED":"live");
    while (digitalRead(BTN_MODE)==LOW) delay(10);
  }}
  bool forceMark=false;
  if (digitalRead(BTN_REC)==LOW){ delay(30); if (digitalRead(BTN_REC)==LOW){
    forceMark=true; while (digitalRead(BTN_REC)==LOW) delay(10);
  }}

  if (!muted){
    int16_t f[FRAME_SAMPLES]; readFrame(f);
    int32_t rms=frameRms(f);
    pushPreroll(f);
    if (VAD_DEBUG){                                       // peak rms ~1/s for threshold tuning
      static int32_t peak=0; static uint32_t lastP=0;
      if (rms>peak) peak=rms;
      if (millis()-lastP>1000){ Serial.printf("rms peak=%ld (ON=%ld OFF=%ld) %s\n",
        (long)peak,(long)VAD_RMS_ON,(long)VAD_RMS_OFF, inSeg?"[REC]":""); peak=0; lastP=millis(); }
    }
    if (forceMark) markedSeg=true;                   // tag this/next segment as deliberate
    static int onsetRun=0;
    if (!inSeg) onsetRun = (rms>VAD_RMS_ON) ? onsetRun+1 : 0;
    bool start = forceMark || (!inSeg && onsetRun>=VAD_ON_FRAMES);
    if (start){
      if (!inSeg){ inSeg=true; segLen=0; seedFromPreroll();
        if (VAD_DEBUG) digitalWrite(LED_REC,HIGH); }      // LED on once onset confirmed
      onsetRun=0; appendSeg(f); hangCount=0;
    } else if (inSeg){
      if (rms>VAD_RMS_OFF){ appendSeg(f); hangCount=0; } // still talking
      else {                                            // ride out the hangover
        appendSeg(f);
        if (++hangCount >= HANG_FRAMES){ closeSegment(markedSeg); markedSeg=false; }
      }
    }
    if (inSeg && segLen >= MAX_SEG_SAMPLES){ closeSegment(markedSeg); markedSeg=false; }
  } else {
    digitalWrite(LED_STAT,HIGH); delay(8); digitalWrite(LED_STAT,LOW); delay(8);
    digitalWrite(LED_STAT,HIGH); delay(8); digitalWrite(LED_STAT,LOW);   // muted double-blink
  }

  // radio-on window (drain + telemetry) only during silence so we never cut a word
  if (!inSeg) serviceNetwork();
  if (millis()-lastHeartbeat>3000 && !muted){ lastHeartbeat=millis();
    digitalWrite(LED_STAT,HIGH); delay(6); digitalWrite(LED_STAT,LOW); }
}
