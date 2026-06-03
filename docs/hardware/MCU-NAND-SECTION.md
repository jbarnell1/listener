# MCU + NAND Section — Schematic Plan

Sheet 2 of the schematic. The ESP32-S3 module + the SPI NAND, wired per
`PINOUT.md` (ADR-007 module, ADR-010 SPI NAND). Same clean-wiring rule: net
labels on short stubs, GND drops to a ground symbol per block.

> Nets that arrive from the Power sheet by name: **3V3, GND, D_M, D_P, SENSE**.
> Nets that leave to the Audio/UI sheet: **I2S_BCLK, I2S_WS, I2S_SD, BTN_USER,
> BTN_MODE, LED_REC, LED_STAT** (we'll place those parts on sheet 3).

## Block A — ESP32-S3-WROOM-1U-N16R8 (U1)
The module symbol has many pins; wire them by **function/GPIO name**, not pad number.

### A1. Power & ground (do this first)
- **Every `3V3` pin → 3V3.** **Every `GND` pin + the thermal pad (EP) → GND.**
- Decoupling at the module 3V3 pins: **10µF bulk + 2–3× 0.1µF**, each `3V3→GND`,
  placed right at the pins (this is the WiFi-TX brownout defense).

### A2. EN / reset (strapping)
- `EN → 10k → 3V3` (pull-up) **and** `EN → 1µF → GND` (power-on delay).
- **RESET button**: `EN → button → GND`.

### A3. BOOT (strapping, GPIO0)
- `GPIO0 → 10k → 3V3` (pull-up; module also has an internal one).
- **BOOT button**: `GPIO0 → button → GND`.
- No auto-program transistors needed — the S3's **native USB** enters download
  mode on its own.

### A4. Native USB (programming + data)
- `GPIO19 → D_M`, `GPIO20 → D_P` (these come from the USBLC6 on the power sheet).
- No external USB pull-ups — the S3 USB PHY has them internally.

### A5. SPI NAND bus (to Block B)
| GPIO | Net |
|------|-----|
| GPIO12 | `SPI_SCK` |
| GPIO11 | `SPI_MOSI` |
| GPIO13 | `SPI_MISO` |
| GPIO10 | `NAND_CS` |

### A6. I2S mic (to Audio sheet)
- `GPIO4 → I2S_BCLK`, `GPIO5 → I2S_WS`, `GPIO6 → I2S_SD`.

### A7. Battery sense
- `GPIO7 → SENSE` (arrives from the power-sheet divider; ADC1).

### A8. Buttons / LEDs (parts live on the UI sheet; just label here)
- `GPIO1 → BTN_USER`, `GPIO2 → BTN_MODE`.
- `GPIO15 → LED_REC`, `GPIO16 → LED_STAT`.

### A9. KEEP-OUT — leave unconnected (N16R8 octal PSRAM + internal flash)
- **GPIO33–37**: octal PSRAM — **do not connect anything** (ADR-007).
- GPIO26–32: internal SPI flash (not exposed/usable).
- Strapping GPIO45/46/3: leave at default, don't load.

## Block B — W25N01GVZEIT NAND (U2, WSON-8)
SPI single-bit (ADR-010). WP#/HOLD# tied high to disable quad.
| Pin | Name | Connect |
|-----|------|---------|
| 1 | CS# | `NAND_CS` + **10k pull-up to 3V3** |
| 2 | DO (IO1) | `SPI_MISO` |
| 3 | WP# (IO2) | **10k pull-up to 3V3** (hold high) |
| 4 | GND | `GND` |
| 5 | DI (IO0) | `SPI_MOSI` |
| 6 | CLK | `SPI_SCK` |
| 7 | HOLD# (IO3) | **10k pull-up to 3V3** (hold high) |
| 8 | VCC | `3V3` + **100nF → GND** at the pin |
| 9 (EP) | exposed pad | `GND` |

- Three 10k pull-ups total (CS#, WP#, HOLD#) — all on hand.
- 100nF decoupling close to pin 8. (Low power; no thermal vias needed on its EP.)

## Build order in EasyEDA Pro
1. New sheet "MCU + NAND". Drop U1 (module) center-left, U2 (NAND) to its right.
2. **A1 first**: label every 3V3 and GND pin; place the module decoupling caps at
   the pins. Get power/ground solid before signals.
3. Add EN (A2) and BOOT (A3) circuits with their buttons.
4. Wire the NAND bus (A5↔Block B) by label — `SPI_SCK/MOSI/MISO`, `NAND_CS`.
5. Place the NAND pull-ups + VCC cap.
6. Label the remaining signal nets (USB, I2S, SENSE, buttons, LEDs) — parts for
   I2S/buttons/LEDs come on sheet 3.
7. DRC: single-pin warnings for I2S_*/BTN_*/LED_* are expected until sheet 3.

## Open
- Confirm which module pads are physically broken out (some GPIOs share the
  WROOM-1U castellations) before final pin lock.
- Q-F1 (ESP-IDF vs Arduino) doesn't affect wiring.
