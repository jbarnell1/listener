/*
 * Listener — board bring-up self-test (POST). No WiFi/creds needed.
 * Flash it, open Serial Monitor @ 115200, watch the report, then press the
 * buttons / talk to the mic and watch the interactive lines.
 *
 * Arduino IDE settings (IMPORTANT for an N16R8 module):
 *   Board:            "ESP32S3 Dev Module"
 *   USB CDC On Boot:  Enabled         <- so Serial prints over the native USB port
 *   Flash Size:       16MB (128Mb)
 *   PSRAM:            "OPI PSRAM"      <- required, or PSRAM reads 0
 *   (If upload won't start: hold BOOT, tap RESET, release BOOT.)
 *
 * Pinout = production board, confirmed from the EasyEDA schematic (ADR-042).
 */
#include <Arduino.h>
#include <SPI.h>

#define NAND_CS   10
#define NAND_SCK  12
#define NAND_MISO 13
#define NAND_MOSI 11
#define MIC_BCLK  4
#define MIC_WS    5
#define MIC_SD    6
#define BTN_REC   1      // active-low, internal pull-up
#define BTN_MODE  2      // active-low, internal pull-up
#define LED_REC   15
#define LED_STAT  16
#define BATT_ADC  7      // ADC1, behind the /2 divider
#define BATT_DIV  2.0f

#define TEST_MIC  1      // set 0 if ESP_I2S won't compile on your core version

SPIClass nandSPI(FSPI);
static inline void csL() { digitalWrite(NAND_CS, LOW); }
static inline void csH() { digitalWrite(NAND_CS, HIGH); }

void testChip() {
  Serial.printf("Chip    : %s rev%d, %d core(s)\n",
                ESP.getChipModel(), ESP.getChipRevision(), ESP.getChipCores());
  Serial.printf("Flash   : %u MB\n", ESP.getFlashChipSize() / (1024 * 1024));
  size_t ps = ESP.getPsramSize();
  Serial.printf("PSRAM   : %u bytes  [%s]\n", ps,
                ps ? "OK" : "NOT FOUND -> set Tools>PSRAM=OPI PSRAM");
  if (ps) {
    const size_t N = 256 * 1024;
    uint8_t *p = (uint8_t *)ps_malloc(N);
    bool ok = p != nullptr;
    if (p) {
      for (size_t i = 0; i < N; i++) p[i] = (uint8_t)i;
      for (size_t i = 0; i < N && ok; i++) ok = (p[i] == (uint8_t)i);
      free(p);
    }
    Serial.printf("PSRAM rw: [%s]\n", ok ? "PASS" : "FAIL");
  }
}

void testNand() {
  pinMode(NAND_CS, OUTPUT); csH();
  nandSPI.begin(NAND_SCK, NAND_MISO, NAND_MOSI, NAND_CS);
  nandSPI.beginTransaction(SPISettings(8000000, MSBFIRST, SPI_MODE0));
  csL(); nandSPI.transfer(0xFF); csH();        // device reset
  delay(3);
  csL();
  nandSPI.transfer(0x9F);                       // read JEDEC ID
  nandSPI.transfer(0x00);                       // dummy address byte
  uint8_t mf = nandSPI.transfer(0), d0 = nandSPI.transfer(0), d1 = nandSPI.transfer(0);
  csH();
  nandSPI.endTransaction();
  Serial.printf("NAND ID : %02X %02X %02X  [%s]   (expect EF AA 21)\n", mf, d0, d1,
                (mf == 0xEF) ? "PASS" : "FAIL -> check SCK=12 MOSI=11 MISO=13 CS=10 + WP#/HOLD# pull-ups");
}

void testBattery() {
  int mv = (int)(analogReadMilliVolts(BATT_ADC) * BATT_DIV);
  Serial.printf("VBAT    : ~%d mV  (USB, no pack: ~floating/charge V; with pack: cell voltage)\n", mv);
}

#if TEST_MIC
#include <ESP_I2S.h>
I2SClass I2S;
bool micOK = false;
void micBegin() {
  I2S.setPins(MIC_BCLK, MIC_WS, -1 /*no DOUT*/, MIC_SD /*DIN*/, -1 /*no MCLK*/);
  micOK = I2S.begin(I2S_MODE_STD, 16000, I2S_DATA_BIT_WIDTH_32BIT,
                    I2S_SLOT_MODE_MONO, I2S_STD_SLOT_LEFT);   // L/R tied GND => LEFT slot
}
long micLevel() {
  if (!micOK) return -1;
  int32_t buf[256];
  size_t n = I2S.readBytes((char *)buf, sizeof(buf));
  int cnt = n / 4; long peak = 0;
  for (int i = 0; i < cnt; i++) { long s = buf[i] >> 14; if (labs(s) > peak) peak = labs(s); }
  return peak;
}
#endif

void report() {
  Serial.println("\n==== Listener board bring-up ====");
  testChip();
  testNand();
  testBattery();
#if TEST_MIC
  Serial.printf("I2S mic : begin [%s]%s\n", micOK ? "OK" : "FAIL",
                micOK ? " -> talk/snap, watch 'mic level' jump" : "");
#endif
  Serial.println("Buttons : REC / MODE events below.  LEDs alternate red<->green.");
  Serial.println("          >>> press REC to RE-PRINT this report <<<");
}

void setup() {
  Serial.begin(115200);
  // Native-USB CDC re-enumerates on every reset, so the Serial Monitor misses early
  // prints. Wait up to 8s for you to (re)open the monitor, then report; also re-print
  // on REC press so you can never miss it.
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 8000) delay(10);
  delay(300);
  pinMode(BTN_REC, INPUT_PULLUP);
  pinMode(BTN_MODE, INPUT_PULLUP);
  pinMode(LED_REC, OUTPUT);
  pinMode(LED_STAT, OUTPUT);
#if TEST_MIC
  micBegin();
#endif
  report();
}

bool lastRec = true, lastMode = true;
uint32_t tLed = 0, tMic = 0;
bool ledTog = false;
long micPeak = 0;

void loop() {
  bool r = digitalRead(BTN_REC), m = digitalRead(BTN_MODE);
  if (r != lastRec)  { lastRec = r;  Serial.printf("REC  %s\n", r ? "released" : "PRESSED");
                       if (!r) report(); }                 // re-print the report on REC press
  if (m != lastMode) { lastMode = m; Serial.printf("MODE %s\n", m ? "released" : "PRESSED"); }

  if (millis() - tLed > 500) {
    tLed = millis(); ledTog = !ledTog;
    digitalWrite(LED_REC, ledTog);
    digitalWrite(LED_STAT, !ledTog);
  }
#if TEST_MIC
  long lvl = micLevel();                       // drain I2S continuously; keep the window peak
  if (lvl > micPeak) micPeak = lvl;
  if (millis() - tMic > 5000) {
    tMic = millis();
    Serial.printf("mic level (5s peak): %ld\n", micPeak);
    micPeak = 0;
  }
#endif
}
