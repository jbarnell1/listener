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

## To-buy list (LCSC)  — derived from EXISTING-HARDWARE.md
Paste-ready cart. Quantities assume building 2–3 boards.

| Item | Why | Suggested qty |
|------|-----|---------------|
| **INMP441 breakout module** (Amazon 5-pack ~$12) | audio input (ADR-012) — mounts on 2×(1×3) header | have |
| **2×(1×3) header / female socket** for the mic module | on-board mount (ADR-012) | 2 rows |
| **TP4056** charge IC (SOP-8) | LiPo charging — LDO can't (ADR-008) | 5 |
| **3.7V 3000mAh protected LiPo** + **JST-PH 2.0** pigtail | power (ADR-008) | 1–2 |
| **JST-PH 2.0 SMD socket** (board side, right-angle) | battery connector | 5 |
| **0603 LEDs** red + green (+ optional blue) | status/record/charge indicators | 10 ea |
| **1.2k 0603** resistor | TP4056 Rprog → 1A charge | 10 |
| **P-MOSFET** AO3401A / DMG2305UX (SOT-23) | load-share Q1 (ADR-011, Q-H10) | 5 |
| **2.4GHz U.FL/IPEX antenna** | WROOM-**1U** has no PCB antenna — needs external | 2–3 |

### Already on hand — no need to buy
- 0603 R: **470** (LED series + TP4056 status LEDs), **5.1k** (USB-C CC1/CC2),
  **10k** (pull-ups: CS#, WP#, HOLD#, EN, BOOT, gate series), **220k** (battery
  divider 220k/220k → ÷2, and Q1 gate pulldown).
- 0603 C: **0.1µF, 1µF, 4.7µF, 10µF, 100µF** — covers all decoupling/bulk.
- Buttons C49234125 (BOOT/RESET/USER/MODE), USB-C, AP2112K, USBLC6, 1N5819, NAND, module.

> NAND in SPI-single mode needs **3× 10k pull-ups** (CS#, WP#, HOLD#) + 100nF — all
> on hand. Q1 load-share needs a **220k gate pulldown** + **10k gate series** — on hand.

## Answers to inventory questions
- **Inductors / ferrite beads:** not required. The AP2112K is a *linear* LDO (no
  inductor). A ferrite bead on the mic VDD is a nice-to-have for noise but optional.
- **Crystals:** none — the ESP32-S3 module has its own.
- **Test points:** optional but recommended — small pads on 3V3, GND, and the
  UART/JTAG lines make bring-up/debug far easier. Cheap insurance; we'll add a few.
- **Rprog (charge current):** 1.2k = 1A. You don't have 1.2k yet (have 5.1k=250mA,
  too slow for 3000mAh). Buy 1.2k, or 2k for a gentler ~0.5A/~6h charge.
