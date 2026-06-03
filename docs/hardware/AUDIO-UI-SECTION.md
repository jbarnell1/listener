# Audio + UI Section — Schematic Plan

Sheet 3 (final schematic sheet). The I2S mic + the user buttons + status LEDs.
All nets arrive from the MCU sheet by name: `I2S_BCLK, I2S_WS, I2S_SD, BTN_USER,
BTN_MODE, LED_REC, LED_STAT`, plus `3V3`/`GND`.

## Block C — INMP441 I2S microphone (MK1)
| Mic pin | Connect |
|---------|---------|
| VDD | `3V3` (+ decoupling below) |
| GND | `GND` |
| SCK | `I2S_BCLK` (GPIO4) |
| WS  | `I2S_WS` (GPIO5) |
| SD  | `I2S_SD` (GPIO6) |
| L/R | `GND` → selects the **left** channel (firmware reads left) |
- Decoupling: **0.1µF + 1µF** from `VDD→GND` at the pin. Optional **ferrite bead**
  in series with VDD for a cleaner mic supply (MEMS mics are noise-sensitive).
- MCU is I2S **master** (drives BCLK/WS); mic is slave (drives SD). No series Rs.

### ⚠️ Mic decision before ordering (Q-H11)
- The INMP441 is **top-port** (acoustic hole faces *up*, away from the PCB) → **no
  PCB hole needed**, but the **enclosure needs an opening above the mic**.
- **Verify LCSC/JLCPCB stock** for the bare INMP441. If it's not stocked (or not
  JLCPCB-assemblable), pick an equivalent I2S MEMS mic that *is* in the JLCPCB
  library (e.g. MSM261S4030H0R, ICS-43434, SPH0645LM4H) — the wiring above is the
  same for any standard I2S MEMS mic.
- Keep the mic away from the antenna and switching/charge nodes in layout.

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
