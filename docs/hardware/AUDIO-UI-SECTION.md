# Audio + UI Section — Schematic Plan

Sheet 3 (final schematic sheet). The I2S mic + the user buttons + status LEDs.
All nets arrive from the MCU sheet by name: `I2S_BCLK, I2S_WS, I2S_SD, BTN_USER,
BTN_MODE, LED_REC, LED_STAT`, plus `3V3`/`GND`.

## Block C — INMP441 module on a 2×(1×3) header (MK1, ADR-012)
Pre-made breakout (Teyleten, board ≈ 0.55 × 0.47 in). Two 1×3 through-hole headers
(2.54mm / 0.1" within each row).
- **Row-to-row spacing: best estimate 0.3" (7.62mm)** — the DIP/breadboard standard,
  fits the 0.47" board, breadboard-compatible. **Measure the real module to confirm
  before fab**; use generous ~1.0mm holes to forgive small error.
- **Alt mount (good for acoustics): wire-out.** Mount the mic at the enclosure's
  sound opening, run 6 thin wires to a simple **1×6 landing strip** on the board.
  Removes the spacing problem and puts the port right at the case hole.
| Module pad | Net |
|-----------|-----|
| VDD | `3V3` |
| GND | `GND` |
| SCK | `I2S_BCLK` (GPIO4) |
| WS  | `I2S_WS` (GPIO5) |
| SD  | `I2S_SD` (GPIO6) |
| L/R | `GND` → selects the **left** channel (firmware reads left) |
- Module has its own decoupling; an optional 0.1µF at the VDD pad is harmless.
- MCU is I2S **master** (drives BCLK/WS); mic is slave (drives SD). No series Rs.
- **Layout:** mic port (center can/hole) must face the **enclosure mic opening**,
  not the main PCB. Keep away from the antenna and charge/switch nodes.
- **Verify footprint 1:1 on paper** against the real module before fab.

## Block D — Buttons (SW3 USER, SW4 MODE)
Each button, active-low into the MCU:
- `3V3 → 10k → BTN_x` (pull-up) and `BTN_x → tact switch → GND`.
- Optional **100nF** `BTN_x → GND` for debounce.
- `BTN_USER` = GPIO1, `BTN_MODE` = GPIO2. (BOOT/RESET buttons live on the MCU sheet.)
- Pressing pulls the line LOW → firmware reads a press.

## Block E — Status LEDs (LED_REC red, LED_STAT green)
Active-high, GPIO-driven:
- `LED_REC → 470 → LED(red) → GND` (GPIO15 high = recording).
- `LED_STAT → 470 → LED(green) → GND` (GPIO16 high = status/connected).
- Anode toward the GPIO/resistor side, cathode to GND. ~3mA at 470Ω — gentle on
  the GPIO. Use **low-Vf colors** (red/green/yellow), never white/blue on 3V3.

## Build order
1. Place MK1; do VDD/GND + decoupling first, then the 3 I2S nets + L/R→GND.
2. Place the two buttons with their pull-ups (+ optional debounce caps).
3. Place the two LEDs with their 470Ω series resistors.
4. DRC the whole 3-sheet schematic together — now there should be **no** single-pin
   nets left (every cross-sheet label has both ends).

## Open
- Q-H11: confirm mic part + JLCPCB assembly stock + enclosure acoustic opening.
