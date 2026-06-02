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

## QSPI NAND (W25N01) — native FSPI quad pins
Using the ESP32-S3 default FSPI mapping keeps the IO-MUX fast path (no GPIO
matrix penalty), important for dumping audio buffers.
| Signal | GPIO | NAND pin |
|--------|------|----------|
| FSPICS0 (/CS) | GPIO10 | CS# |
| FSPICLK (CLK) | GPIO12 | CLK |
| FSPID  (IO0/DI)  | GPIO11 | DIO0 |
| FSPIQ  (IO1/DO)  | GPIO13 | DIO1 |
| FSPIWP (IO2)  | GPIO14 | DIO2 (WP#) |
| FSPIHD (IO3)  | GPIO9  | DIO3 (HOLD#) |

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
GPIO8, GPIO17, GPIO18, GPIO21, and GPIO38–48 (subject to module pad availability)
remain for: charger status sense, a third LED, BLE-status pin, or future I2C.

## Conflict check
- USB (19/20) — isolated. ✔
- FSPI NAND (9–14) — no overlap with I2S (4–6) or UI. ✔
- I2S (4–6) — clear of strapping. ✔
- ADC battery sense on ADC1 (GPIO7) — ADC2 avoided (WiFi conflict). ✔

**To finalize:** confirm module variant (Q-H1) and which physical module pads
are broken out, then lock this table before schematic capture.
