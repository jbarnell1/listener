# Bill of Materials (working draft)

Two columns matter: **what the design needs** and **what you already have**.
Anything not on hand becomes the LCSC shopping list. Quantities/values firm up
after `EXISTING-HARDWARE.md` and the schematic are done.

## Core ICs / modules
| Ref | Part | LCSC | Footprint | Notes | On hand? |
|-----|------|------|-----------|-------|----------|
| U1 | ESP32-S3-WROOM-1U-N16R8 | C3013946 | module | MCU + WiFi/BLE, octal PSRAM | ✅ x3 |
| U2 | Winbond W25N01GVZEIT | — | WSON-8 6x8 | 128MB NAND, QSPI | ✅ x3 |
| MK1 | INMP441 I2S mic | TBD | SMD | digital mic | ❌ **BUY** |
| J1 | USB-C receptacle | C2765186 | SMD | power + data | ✅ x10+ |
| U3 | **TP4056** charge IC (ADR-008) | TBD | SOP-8 (ESON) | LiPo CC/CV charger | ❌ **BUY** |
| U4 | AP2112K-3.3TRG1 | C23380830 | SOT-23-5 | 3.3V LDO (not a charger!) | ✅ x3 |
| D1 | USBLC6-2SC6 | C2827654 | SOT-23-6 | USB ESD/TVS | ✅ x3 |
| D5 | 1N5819WS Schottky | C191023 | SOD-323 | VBUS→VSYS (USB powers system) | ✅ |
| Q1 | P-MOSFET (AO3401A/DMG2305UX) | TBD | SOT-23 | battery load-share (ADR-011) | ❌ **BUY** |

## Power section passives
| Ref | Value | Footprint | Purpose |
|-----|-------|-----------|---------|
| Rcc1, Rcc2 | 5.1k | 0603 | USB-C CC1/CC2 pull-downs (sink advertise) |
| Rprog | 1.2k–5k | 0603 | TP4056 charge-current set (see Q-H2 battery) |
| Cin | 10uF | 0603/0805 | charger input bulk |
| Cbat | 10uF | 0603/0805 | battery rail bulk |
| Cldo_in/out | 1uF + 10uF | 0603 | LDO in/out stability |
| Rdiv1/Rdiv2 | 100k/100k (or 200k/100k) | 0603 | battery-sense divider to ADC |

## Decoupling (defensive, per ARCHITECTURE / hand-off)
| Where | Caps |
|-------|------|
| ESP32-S3 3V3 pins | 10uF bulk + multiple 0.1uF close to pins |
| W25N01 VCC | 10uF + 0.1uF |
| INMP441 VDD | 1uF + 0.1uF |

## User interface
| Ref | Part | Notes |
|-----|------|-------|
| SW1 | tactile | BOOT (GPIO0 strap) |
| SW2 | tactile | RESET (EN) |
| SW3 | tactile | USER / push-to-talk |
| SW4 | tactile | MODE (cycle VAD/continuous/mute) |
| LED1 | red 0603 | recording active |
| LED2 | green 0603 | status / connected |
| LED3 | (charger CHRG/STBY) | from TP4056 pins directly |
| Rled* | 330–1k 0603 | LED series resistors |

## To-buy list (LCSC) — reconciled against exported board BOM + on-hand stock (2026-06-03)
Paste-ready cart. Buy ~3–5× each (building up to 3 boards; LCSC has order minimums).

| Item | LCSC | Qty/board | For |
|------|------|-----------|-----|
| **TP4056** charger | C5311018 | 1 | U1 |
| **AO3401A** P-MOSFET | C15127 | 1 | Q1 |
| **1.2kΩ** 0603 (YAGEO RC0603FR-071K2L) | C114605 | 1 | R1 (PROG) — sub, C22765 OOS |
| **220kΩ** 0603 | C22961 | 3 | R7, R8, R10 (divider + Q1 gate pulldown) |
| **Red LED** KT-0603R (2.4V) | C2286 | 2 | REC, STDBY |
| **Green LED** SML-E12P8WT86 (2.2V) | C126319 | 2 | CHRG, STATUS — low-Vf (emerald 3.1V was too dim) |
| 1×3 headers (H1, H2) | — | reuse | mic module's included pins |
| **INMP441 breakout module** | Amazon | have | mic (ADR-012) |
| **407090 3000mAh LiPo** | Amazon | ordered | power |
| **2.4GHz U.FL antenna** | — | have | on hand from prior project |

> ⚠️ Hand-assembly: the green LED (ROHM) is a different brand than the footprint's
> original part — verify its cathode mark against the footprint when placing.

### Already on hand — exact LCSC match
100nF (C14663), 1µF (C15849), 10µF (C19702), 470Ω (C23179), JST S2B-PH (C173752),
1N5819 (C191023), USB-C (C2765186), USBLC6 (C2827654), ESP32 (C3013946),
NAND (C17656808), AP2112K (C23380830), tact switch (C49234125).

### On hand — substitute (same value, different code)
- **5.1kΩ**: have C2907044 (BOM auto-picked C23186) → use yours (R2, R4).
- **10kΩ**: have C98220 (BOM auto-picked C25804) → use yours (R6, R9, R11–R18).

### Notes
- On-hand `C22962` confirmed = **220 Ω (not kΩ)** → unused by design; **must buy
  220kΩ** (C22961). Can't sub 10k: gate pulldown R10 needs to be ≫ the 10k gate
  series, and the divider needs low drain.
- U.FL antenna: **on hand** from a prior project.
- Have but **unused** by this design: 100µF (C15008), 4.7µF (C19666), 220Ω (C22962).

## Answers to inventory questions
- **Inductors / ferrite beads:** not required. The AP2112K is a *linear* LDO (no
  inductor). A ferrite bead on the mic VDD is a nice-to-have for noise but optional.
- **Crystals:** none — the ESP32-S3 module has its own.
- **Test points:** optional but recommended — small pads on 3V3, GND, and the
  UART/JTAG lines make bring-up/debug far easier. Cheap insurance; we'll add a few.
- **Rprog (charge current):** 1.2k = 1A. You don't have 1.2k yet (have 5.1k=250mA,
  too slow for 3000mAh). Buy 1.2k, or 2k for a gentler ~0.5A/~6h charge.
