# Power Section — Schematic Plan

Implements ADR-008 (3000mAh LiPo) + ADR-011 (TP4056 charge + P-MOSFET load-share).
Reuses your proven Hub topology and adds in-place USB-C charging with a drop-free
battery path.

> **Clean-wiring rule:** wire by **net label**, not long dragged wires. Place a
> net-label on a short stub at each pin (`VBUS`, `VBAT`, `VSYS`, `+3V3`, `GND`).
> Same name = same net. This is how we avoid criss-crossing lines.

## Power flow (one direction, left → right)
```
USB-C ──VBUS──┬─► TP4056 (charger) ──VBAT──► [Battery JST]
              │        │                         │
              │        └──VBAT───────────┐       │
              │                          │       │
              ├──► D5 (1N5819) ──VSYS──┐ │       │
              │                        │ │       │
        Q1 P-MOSFET load-share: ───────┤ │       │
          S=VSYS  D=VBAT  ─────────────┘ │       │
          gate = VBUS-sensed ◄───────────┘       │
              │                                   │
            VSYS ──► AP2112K LDO ──► +3V3 ──► everything
```
- **Plugged in:** USB feeds `VSYS` through D5; TP4056 charges the battery; Q1 is
  OFF (battery isolated from the load → clean charge current).
- **Unplugged:** Q1 turns ON, battery feeds `VSYS` directly through the FET with
  **no diode drop** (the whole point of ADR-011).

## Nets
`VBUS` (USB 5V) · `VBAT` (battery / TP4056 BAT) · `VSYS` (system rail) ·
`+3V3` (regulated) · `GND` · plus data `D_P`/`D_M`, `CC1`/`CC2`.

## Block 1 — USB-C connector (J_USB, C2765186)
| Pin | Net | Notes |
|-----|-----|-------|
| VBUS ×2 | VBUS | tie both VBUS pins together |
| GND ×2 + SHELL | GND | |
| CC1 | via **R 5.1k → GND** | sink/UFP advertise |
| CC2 | via **R 5.1k → GND** | (separate resistor each — never share) |
| D+ / D- | D_P / D_M | go to USBLC6 first (below) |

## Block 2 — ESD (D_ESD, USBLC6-2SC6)
- Connector D+/D- → USBLC6 I/O pins → `D_P`/`D_M` to the ESP32 USB (GPIO20/GPIO19).
- USBLC6 VBUS pin → `VBUS`; GND → `GND`. (Mirror your Hub USB-C sheet exactly.)

## Block 3 — Charger (U_CHG, TP4056, SOP-8)
| Pin | Connect |
|-----|---------|
| 4 VCC (IN) | `VBUS` |
| 3 GND | `GND` |
| 5 BAT | `VBAT` |
| 2 PROG | **R 1.2k → GND**  (sets 1A charge) |
| 8 CE | `VBUS` (enable) |
| 1 TEMP | `GND` (thermistor unused) |
| 7 CHRG | `+3V3 → R 470 → LED(charging) → CHRG` (open-drain sink) |
| 6 STDBY | `+3V3 → R 470 → LED(full) → STDBY` |
- Caps: **10µF** `VBUS→GND` and **10µF** `VBAT→GND`, close to the IC.

## Block 4 — Load-share (the ADR-011 core)
- **D5 (1N5819):** anode `VBUS`, cathode `VSYS`.
- **Q1 (P-MOSFET, AO3401A/DMG2305UX):** **Source = `VSYS`, Drain = `VBAT`.**
- **Gate network:** `VBUS — 10k — GATE` and `GATE — 220k — GND`.
  - Plugged: gate ≈ VBUS (> source) → Q1 **OFF**, body diode reverse-biased (VSYS
    4.65V > VBAT ≤4.2V) → **no back-feed into the battery**. ✔
  - Unplugged: gate pulled to GND → Vgs ≈ −VBAT → Q1 **ON** → battery powers VSYS
    with no drop. ✔
- All gate/divider resistors (10k, 220k) are on hand.

## Block 5 — LDO (U_LDO, AP2112K-3.3, C23380830)
| Pin | Connect |
|-----|---------|
| 1 VIN | `VSYS` |
| 2 GND | `GND` |
| 3 EN | `VSYS` via **10k** (always-on) |
| 5 VOUT | `+3V3` |
| 4 NC | leave |
- Caps: **1µF** `VSYS→GND` (in), **10µF + 100nF** `+3V3→GND` (out).

## Block 6 — Battery monitor (sense)
- Divider **VBAT — 220k — SENSE — 220k — GND**; `SENSE → GPIO7 (ADC1)`; add
  **100nF** SENSE→GND. Reads ~2.1V at 4.2V battery (within ADC range).

## Block 7 — Battery connector (J_BAT)
- **JST-PH 2.0** (S2B-PH-K-S style, like your Hub J1): pin1 `VBAT`, pin2 `GND`.
  Confirm polarity against the actual LiPo pigtail before first plug-in!

## EasyEDA Pro build order (keeps it tidy)
1. Make a **dedicated "Power" sheet** (multi-sheet schematic — power / MCU+NAND /
   audio+UI). Title block like your Hub sheets.
2. Drop J_USB, D_ESD, U_CHG, Q1, D5, U_LDO, J_BAT roughly left→right per the flow.
3. Place a short wire stub + **net label** on every power pin first; only then
   draw the few remaining local wires. Power flows left→right, GND down to a
   ground symbol on each block — no long horizontal GND wires.
4. Add the decoupling caps **physically adjacent** to each IC's pin in the
   schematic so layout placement is obvious later.
5. Run EasyEDA's "one-net-name check" — any net with a single pin is a wiring bug.

## Open
- Q-H10: confirm exact P-MOSFET part (AO3401A vs DMG2305UX) for LCSC.
- TP4056 vs a tiny MCP73831 if board space gets tight (we can revisit at layout).
