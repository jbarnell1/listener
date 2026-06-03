# PCB Layout Plan

Schematic is complete + DRC-clean. This sheet covers converting to PCB, placement,
routing, design rules, and the JLCPCB export.

## 0. Convert
In EasyEDA Pro: **Convert schematic → PCB**. All parts come in with a ratsnest
(thin lines showing required connections). Set 2-layer board to start.

## 1. Placement — the golden rule
**Decoupling caps touch their IC's power pin, smallest cap closest, with a GND via
right at the cap's ground pad.** Place each IC, then immediately cluster its passives.

### Decoupling placement (by designator)
| IC | At the pin | Pin |
|----|-----------|-----|
| U5 ESP32-S3 | C9 (100nF) closest + C11 (10µF) | 3V3 (2) |
| U5 | C12 (100nF) | other 3V3 pin |
| U5 | C8 (1µF) + R13 (10k) | EN (3) |
| U5 | R15 (10k) | IO0 (27) |
| U4 NAND | C10 (100nF) | VCC (8) |
| U4 | R11/R12/R14 (10k pull-ups) | near NAND |
| U1 TP4056 | C1 (10µF) | VCC (4) |
| U1 | C2 (10µF) | BAT (5) |
| U1 | PROG R (1.2k) | PROG (2) |
| U2 AP2112K | C3 (1µF) + C6 (10µF) | VIN (1) |
| U2 | C4 (100nF) closest + C7 (10µF) | VOUT (5) |
| U2 | R6 (10k) | EN (3) |

### Special-case placement
- **D1 USBLC6 + 5.1k CC R's:** right at the USB-C connector, inline on D+/D−.
- **C5 (100nF sense filter):** near U5's IO7 (ADC end of `SENSE`), not the battery.
- **U1 TP4056 EP:** GND pour + 4–9 thermal vias (enables 1A charge).
- **Load-share Q1/D5 + gate R's (10k/220k):** in the VBUS/VBAT/VSYS power path,
  short wide traces; gate R's at Q1's gate.
- **LEDs:** placed for visibility through the enclosure; 470Ω next to each.
- **Mic headers:** near the enclosure mic opening, away from power/antenna.

## 1b. Board outline + floorplan (decided)
- **Outline: 87 × 65 mm**, 2-layer, ~3mm rounded corners, **M2 holes in 4 corners**.
- **Battery stacked behind**: 407090 3000mAh, **92×70×4mm** (JST-PH2.0). The board
  (87×65) sits just **inside** the battery footprint (~2.5mm margin each side), so
  the **battery sets the device size (~92×70mm)** while the board uses the full
  area for easy routing. Still the cheapest JLCPCB tier (≤100×100mm).
- All components on the **TOP layer**; bottom kept flat for the pack (foam tape) +
  ground pour. Single-sided assembly = cheaper.
- Put the **JST connector near the battery's pigtail exit** (one 70mm end).
```
 ┌──────────────────────────────────────────────────────────┐
 │ [MIC hdrs]                          U.FL→  [LED1][LED2]    │
 │                  ┌────────────┐   ┌──────┐  [REC][STAT]   │
 │ [BTN_USER]       │ U5 ESP32-S3│   │ U4   │                │
 │ [BTN_MODE]       │ +decoupling│   │ NAND │                │
 │ [BOOT][RESET]    └────────────┘   └──────┘                │
 │ [USB-C] D1  U1 TP4056   Q1/D5   U2 LDO        [JST batt]  │
 └──────────────────────────────────────────────────────────┘
```
- Bottom edge = full power chain L→R: USB-C → D1 → TP4056 → load-share → LDO → JST.
- Center: U5 + decoupling; U4 NAND right beside it (short SPI).
- Top edge: mic at the case opening, module U.FL end facing this edge, LEDs visible.
- Left edge: the four buttons.

## 2. Placement strategy (clusters)
- **Power cluster** (one corner near the USB-C edge): USB-C → D1 → U1 → load-share
  → U2. Keeps high-current loops tight.
- **Battery JST (U3):** board edge near U1/load-share.
- **MCU+NAND:** center. Put U4 NAND **right next to U5's IO10–13** so SPI traces are
  short. Keep digital signals over a solid ground.
- **Buttons:** accessible edge/face. **LEDs:** visible face. **Mic:** at its opening.
- **WROOM-1U antenna:** it's the **U.FL external-antenna** variant, so **no PCB
  antenna keep-out is needed** (RF leaves via the U.FL coax). Just leave physical
  room for the U.FL connector + cable at the module's antenna edge.

## 3. Routing
- **Power nets wider:** VBUS/VBAT/VSYS/3V3 ~0.4–0.5mm (1A charge path); signals
  ~0.2–0.25mm.
- **Ground:** copper **pour GND on both layers**, stitch with vias; solid ground
  under the MCU and SPI/I2S signals (short return paths).
- Keep SPI (SCK/MOSI/MISO/CS) and I2S short and roughly equal; keep them away from
  the charge/switch nodes.
- USB D+/D−: route as a rough pair, short, through D1.

## 4. Design rules (JLCPCB)
- Use EasyEDA's **JLCPCB design-rule preset**. Safe targets: trace/space ≥0.2mm
  (min 0.127mm), via ≥0.3mm hole / 0.6mm pad, hole-to-edge ≥0.3mm.
- Run **PCB DRC** until clean.

## 5. Export for fab + assembly
- **Gerbers** (fab), **BOM**, **CPL/Pick-and-place** (assembly).
- For JLCPCB assembly: match LCSC part numbers in the BOM; mark hand-placed/
  through-hole parts (mic module, JST, buttons, headers) as needed.
- Order: 2-layer, 1.6mm (or thinner for a slim wearable), HASL or ENIG.

## Open
- Final board outline / size (credit-card-ish) + mounting + enclosure.
- Confirm thinner board (e.g. 1.0mm) if slimness matters.
