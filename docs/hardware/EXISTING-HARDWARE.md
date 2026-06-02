# On-Hand Hardware Inventory

Fill this in from your workbench so we only design with parts you already have.
For passives, list values + footprint + rough quantity. For ICs/modules, the
**exact** part number and LCSC code if known.

> When in doubt, photograph the reel/label and note it here. The pinout and BOM
> depend on the exact variants below.

## Microcontroller module
- **ESP32-S3-WROOM-1U** variant: `__________` (e.g. N16R8 / N8 / N8R8)
  - Flash size: ____ MB · PSRAM: ____ MB · PSRAM type: quad / octal / none
  - Qty on hand: ____
  - *Why it matters:* octal PSRAM (R8) uses GPIO33–37 → fewer free pins.

## Storage
- **Winbond W25N01GVZEIT** 1Gbit NAND, 8-WSON 6x8mm — Qty: ____

## Audio
- **INMP441** I2S mic (module or bare?): `__________` — Qty: ____

## Power / USB
- USB-C receptacle part: `__________` — Qty: ____
- Charge controller: TP4056 (module / bare IC) or PMIC `__________` — Qty: ____
- LDO 3.3V: AP2112K-3.3 / MCP1700-3302 / other `__________` — Qty: ____
- ESD protection: USBLC6-2SC6 or `__________` — Qty: ____
- Battery: ____ mAh LiPo, size ____ mm, connector `__________`

## User interface
- Tactile buttons (footprint / part): `__________` — Qty: ____
- 0603 LEDs (colors on hand): `__________`

## Passives (0603)
Resistors (value : qty) — fill the ones you have:
- 5.1k (USB-C CC): ____
- 10k (pull-ups): ____
- 1k / 330 / 220 (LED series): ____
- 100k / 200k (battery divider): ____
- charge Rprog (1.2k≈1A, 2k≈580mA, 5k≈250mA): ____
- others: ____

Capacitors (value : qty):
- 0.1uF: ____
- 1uF: ____
- 10uF: ____
- 22uF / others: ____

## Misc
- Inductors / ferrite beads: ____
- Crystals (S3 module has internal, usually none needed): ____
- Connectors / test points: ____
