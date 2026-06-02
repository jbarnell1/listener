# On-Hand Hardware Inventory

Fill this in from your workbench so we only design with parts you already have.
For passives, list values + footprint + rough quantity. For ICs/modules, the
**exact** part number and LCSC code if known.

> When in doubt, photograph the reel/label and note it here. The pinout and BOM
> depend on the exact variants below.

## Microcontroller module
- **ESP32-S3-WROOM-1U** variant: `N16R8` (e.g. N16R8 / N8 / N8R8)
  - Flash size: LCSC part C3013946  · PSRAM type: quad / octal / none
  - Qty on hand: 3
  - *Why it matters:* octal PSRAM (R8) uses GPIO33–37 → fewer free pins.

## Storage
- **Winbond W25N01GVZEIT** 1Gbit NAND, 8-WSON 6x8mm — Qty: 3

## Audio
- **INMP441** I2S mic (module or bare?): `__________` — Qty: none

## Power / USB
- USB-C receptacle part: LCSC C2765186 — Qty: a bunch >10
- Charge controller: TP4056 (module / bare IC) or PMIC `bare IC? I just used my own from resistors/capacitors and AP2112k/ESD protector, so battery can wire in or usbc can charge/power if needed` — Qty: None (build our own?)
- LDO 3.3V: AP2112K-3.3 / MCP1700-3302 / other `AP2112K-3.3TRG1, C23380830` — Qty: 3
- ESD protection: USBLC6-2SC6 or `C2827654 (yes USBLC6-2SC6)` — Qty: 3
- Battery: 3000 mAh LiPo, size credit card, connector JST - still need!
- also have a diode called 1N58 19WS 40V 1A surface mount SOD-323 C191023

## User interface
- Tactile buttons (footprint / part): `C49234125` — Qty: lots
- 0603 LEDs (colors on hand): `__________` -none atm

## Passives (0603)
Resistors (value : qty) — fill the ones you have:
- 470: lots
- 5.1k (USB-C CC): lots
- 10k (pull-ups): lots
- 1k / 330 / 220 (LED series): ____
- 220k (battery divider): lots
- charge Rprog (1.2k≈1A, 2k≈580mA, 5k≈250mA): ____
- others: ____

Capacitors (value : qty):
- 0.1uF: lots
- 1uF: lots
- 4.7uF: lots
- 10uF: lots
- 100uF: lots
- 22uF / others: none

## Misc
- Inductors / ferrite beads: what do we need this for?
- Crystals (S3 module has internal, usually none needed): none needed
- Connectors / test points: What is this needed for?
