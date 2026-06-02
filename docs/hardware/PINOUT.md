# ESP32-S3 Pinout (PRELIMINARY)

> ⚠️ Preliminary — depends on **Q-H1** (module variant / PSRAM type). If the
> module is an **R8 (octal PSRAM)** part, GPIO33–37 are consumed internally and
> must NOT be used externally. The map below avoids them to stay variant-safe.

## Reserved / do-not-touch
| Pin | Use |
|-----|-----|
| GPIO19, GPIO20 | **Native USB** D-/D+ (programming + USB-Serial-JTAG) |
| GPIO0 | **Strapping** (BOOT button) — fine as button, don't load heavily |
| GPIO45, GPIO46, GPIO3 | **Strapping** — avoid or use with care |
| GPIO26–32 | module SPI flash/PSMRAM (internal) — not exposed/usable |
| GPIO33–37 | **only if octal PSMRAM (R8)** — keep free to be safe |
| EN | RESET |

## NAND (W25N01) — standard SPI single-bit (ADR-010)
Reuses the proven Hub wiring. Quad dropped (Opus data rate is tiny). Uses the
fast IO-MUX FSPI pins for SCK/MOSI/MISO/CS; IO2/IO3 are tied high, not routed to
GPIOs — which **frees GPIO9 and GPIO14**.
| Signal | GPIO | NAND pin | Notes |
|--------|------|----------|-------|
| SPI_SCK  | GPIO12 | CLK (pin6) | FSPICLK |
| SPI_MOSI | GPIO11 | DI/IO0 (pin5) | FSPID |
| SPI_MISO | GPIO13 | DO/IO1 (pin2) | FSPIQ |
| NAND_CS  | GPIO10 | CS# (pin1) | FSPICS0; **10k pull-up to 3V3** |
| WP#(IO2) | — | pin3 | **10k pull-up to 3V3** (disable, single-bit) |
| HOLD#(IO3) | — | pin7 | **10k pull-up to 3V3** (disable, single-bit) |
| VCC | — | pin8 | 3V3 + 100nF decoupling |
| GND / EP | — | pin4 / pin9 (EP) | both to GND |

## I2S microphone (INMP441) — input only
| Signal | GPIO | Mic pin |
|--------|------|---------|
| I2S BCLK (SCK) | GPIO4 | SCK |
| I2S WS (LRCL)  | GPIO5 | WS |
| I2S SD (DOUT→MCU DIN) | GPIO6 | SD |
| L/R select | tie to GND (left) | L/R |
> Any GPIO can carry I2S via the matrix; GPIO4/5/6 chosen to sit away from
> strapping/USB and keep routing short to the mic.

## User interface
| Function | GPIO | Notes |
|----------|------|-------|
| BOOT button | GPIO0 | strapping, internal/ext pull-up |
| USER / push-to-talk | GPIO1 | active-low, ext 10k pull-up |
| MODE button | GPIO2 | cycles VAD/continuous/mute |
| LED recording (red) | GPIO15 | + series R |
| LED status (green) | GPIO16 | + series R |
| Battery sense (ADC) | GPIO7 | ADC1_CH6, via divider; add 100nF |

## Free / spare (variant-dependent)
**GPIO9, GPIO14** (freed by SPI-single NAND, ADR-010), GPIO8, GPIO17, GPIO18,
GPIO21, and GPIO38–48 (subject to module pad availability) remain for: charger
status sense, a third LED, BLE-status pin, or future I2C.

## Conflict check
- USB (19/20) — isolated. ✔
- SPI NAND (10–13) — no overlap with I2S (4–6) or UI. ✔
- I2S (4–6) — clear of strapping. ✔
- ADC battery sense on ADC1 (GPIO7) — ADC2 avoided (WiFi conflict). ✔

**To finalize:** confirm which physical module pads are broken out, then lock
this table before schematic capture. (Module = N16R8, GPIO33–37 reserved — ADR-007.)
