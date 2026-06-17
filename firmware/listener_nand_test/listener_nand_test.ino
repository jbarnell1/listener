// listener_nand_test.ino — W25N01 NAND round-trip verification (Stage 1, ADR-045).
//
// Proves the storage layer on the ASSEMBLED board before we build the ring buffer on it.
// Until now only the JEDEC ID (EF AA 21) had been read — never an actual write/read/erase.
//
// What it does (on boot, ONCE; re-runs when you press REC):
//   1. init + JEDEC ID check
//   2. erase block 0
//   3. confirm erased page reads back all 0xFF
//   4. write a known pseudo-random pattern across 4 pages (8 KB)
//   5. read it back and compare byte-for-byte
//   6. erase again and confirm 0xFF
//   7. print PASS/FAIL with the first mismatch (if any)
//
// No external libraries, no WiFi. Board: ESP32S3 Dev Module · USB CDC On Boot=Enabled ·
// Flash 16MB · PSRAM=OPI PSRAM. Safe: only touches block 0 (the ring buffer will own a
// dedicated region anyway). LED_REC blinks slow=PASS, fast=FAIL.

#include <SPI.h>

// ---- NAND wiring (production board, ADR-010/ADR-042): CS=10 SCK=12 MISO=13 MOSI=11 ----
#define NAND_CS   10
#define NAND_SCK  12
#define NAND_MISO 13
#define NAND_MOSI 11
#define BTN_REC   1
#define LED_REC   15
#define LED_STAT  16

SPIClass nandSPI(FSPI);
SPISettings nandCfg(20000000, MSBFIRST, SPI_MODE0);
const uint16_t NAND_PAGE = 2048;
const uint16_t PAGES_PER_BLOCK = 64;

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
void nandWaitBusy(){ while (nandStatus(0xC0) & 0x01) delayMicroseconds(50); }   // SR-3 bit0 = BUSY
void nandReset(){
  nandSPI.beginTransaction(nandCfg); csL(); nandSPI.transfer(0xFF); csH(); nandSPI.endTransaction();
  delay(2); nandWaitBusy();
}
void nandReadId(uint8_t* id){
  nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0x9F); nandSPI.transfer(0x00);
  id[0]=nandSPI.transfer(0); id[1]=nandSPI.transfer(0); id[2]=nandSPI.transfer(0);
  csH(); nandSPI.endTransaction();
}
void nandEraseBlock(uint16_t page){
  nandWriteEnable();
  nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0xD8); nandSPI.transfer(0x00);
  nandSPI.transfer((page>>8)&0xFF); nandSPI.transfer(page&0xFF);
  csH(); nandSPI.endTransaction();
  nandWaitBusy();
}
// SR-3 (0xC0) after program/erase: bit3 (0x08)=program-fail, bit2 (0x04)=erase-fail.
uint8_t nandWritePage(uint16_t page, const uint8_t* data, uint16_t len){
  nandWriteEnable();
  nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0x02); nandSPI.transfer(0x00); nandSPI.transfer(0x00);   // program load @col 0
  for (uint16_t i=0;i<len;i++) nandSPI.transfer(data[i]);
  csH(); nandSPI.endTransaction();
  nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0x10); nandSPI.transfer(0x00);                           // execute
  nandSPI.transfer((page>>8)&0xFF); nandSPI.transfer(page&0xFF);
  csH(); nandSPI.endTransaction();
  nandWaitBusy();
  return nandStatus(0xC0);
}
void nandReadPage(uint16_t page, uint8_t* data, uint16_t len){
  nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0x13); nandSPI.transfer(0x00);                           // page -> data buffer
  nandSPI.transfer((page>>8)&0xFF); nandSPI.transfer(page&0xFF);
  csH(); nandSPI.endTransaction();
  nandWaitBusy();
  nandSPI.beginTransaction(nandCfg); csL();
  nandSPI.transfer(0x03); nandSPI.transfer(0x00); nandSPI.transfer(0x00); nandSPI.transfer(0x00);  // read buffer @col0
  for (uint16_t i=0;i<len;i++) data[i]=nandSPI.transfer(0x00);
  csH(); nandSPI.endTransaction();
}
bool nandInit(){
  pinMode(NAND_CS, OUTPUT); csH();
  nandSPI.begin(NAND_SCK, NAND_MISO, NAND_MOSI, NAND_CS);
  nandReset();
  uint8_t id[3]; nandReadId(id);
  Serial.printf("NAND JEDEC ID: %02X %02X %02X (expect EF AA 21)\n", id[0], id[1], id[2]);
  if (id[0] != 0xEF){ Serial.println("NAND: not detected — check wiring/pins."); return false; }
  nandWriteStatus(0xA0, 0x00);   // SR-1: clear block-protect (BP) bits — unlock all blocks
  uint8_t sr1 = nandStatus(0xA0);
  Serial.printf("NAND SR-1 (protect) after unlock: 0x%02X %s\n", sr1, sr1 ? "(BP bits still set!)" : "(unlocked)");
  return true;
}

// deterministic test pattern: page-dependent so a page-address bug shows as a mismatch
static uint8_t patByte(uint16_t page, uint16_t i){ return (uint8_t)((i * 31 + page * 167 + 7) & 0xFF); }

uint8_t bufW[2048], bufR[2048];

bool runNandTest(){
  const uint16_t BLK = 0;                         // test block 0 only
  const uint16_t base = BLK * PAGES_PER_BLOCK;    // first page of the block
  const uint16_t NPAGES = 4;                      // 8 KB across 4 pages

  Serial.println("\n--- NAND round-trip test (block 0) ---");

  // 2. erase
  nandEraseBlock(base);
  uint8_t sr = nandStatus(0xC0);
  if (sr & 0x04){ Serial.printf("ERASE FAILED (SR-3=0x%02X)\n", sr); return false; }

  // 3. erased page must read 0xFF
  nandReadPage(base, bufR, NAND_PAGE);
  for (uint16_t i=0;i<NAND_PAGE;i++) if (bufR[i] != 0xFF){
    Serial.printf("POST-ERASE not 0xFF at byte %u: got 0x%02X\n", i, bufR[i]); return false;
  }
  Serial.println("erase OK (reads 0xFF)");

  // 4. write pattern across NPAGES
  for (uint16_t p=0;p<NPAGES;p++){
    for (uint16_t i=0;i<NAND_PAGE;i++) bufW[i] = patByte(base+p, i);
    uint8_t st = nandWritePage(base+p, bufW, NAND_PAGE);
    if (st & 0x08){ Serial.printf("PROGRAM FAILED page %u (SR-3=0x%02X)\n", base+p, st); return false; }
  }
  Serial.printf("wrote %u pages (%u bytes)\n", NPAGES, NPAGES*NAND_PAGE);

  // 5. read back + verify
  for (uint16_t p=0;p<NPAGES;p++){
    nandReadPage(base+p, bufR, NAND_PAGE);
    for (uint16_t i=0;i<NAND_PAGE;i++){
      uint8_t want = patByte(base+p, i);
      if (bufR[i] != want){
        Serial.printf("MISMATCH page %u byte %u: wrote 0x%02X read 0x%02X\n",
                      base+p, i, want, bufR[i]);
        return false;
      }
    }
  }
  Serial.printf("read-back verified: %u bytes match\n", NPAGES*NAND_PAGE);

  // 6. erase again -> 0xFF
  nandEraseBlock(base);
  nandReadPage(base, bufR, NAND_PAGE);
  for (uint16_t i=0;i<NAND_PAGE;i++) if (bufR[i] != 0xFF){
    Serial.printf("RE-ERASE not 0xFF at byte %u\n", i); return false;
  }
  Serial.println("re-erase OK (reads 0xFF)");
  return true;
}

void blinkResult(bool pass){
  // PASS = slow 1 Hz on LED_STAT; FAIL = fast 5 Hz on LED_REC. (one 3 s burst)
  uint32_t t0 = millis();
  while (millis() - t0 < 3000){
    if (pass){ digitalWrite(LED_STAT, HIGH); delay(250); digitalWrite(LED_STAT, LOW); delay(250); }
    else     { digitalWrite(LED_REC,  HIGH); delay(80);  digitalWrite(LED_REC,  LOW); delay(80);  }
  }
}

void doTest(){
  bool ok = nandInit() && runNandTest();
  Serial.printf("\n=== NAND TEST: %s ===\n", ok ? "PASS ✅" : "FAIL ❌");
  if (ok) Serial.println("Storage layer verified — clear to build the ring buffer (Stage 2).");
  else    Serial.println("Stop here — fix NAND before Stage 2 (recheck CS/SCK/MISO/MOSI, pull-ups on WP#/HOLD#/CS).");
  blinkResult(ok);
}

void setup(){
  Serial.begin(115200);
  pinMode(BTN_REC, INPUT_PULLUP);
  pinMode(LED_REC, OUTPUT); pinMode(LED_STAT, OUTPUT);
  unsigned long t0 = millis(); while (!Serial && millis() - t0 < 4000) delay(10);
  Serial.println("\n=== Listener NAND round-trip test (Stage 1) ===");
  doTest();
  Serial.println(">>> press REC to re-run <<<");
}

void loop(){
  if (digitalRead(BTN_REC) == LOW){
    delay(30); if (digitalRead(BTN_REC) != LOW) return;
    doTest();
    while (digitalRead(BTN_REC) == LOW) delay(10);
  }
  delay(20);
}
