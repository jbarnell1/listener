# Bill of Materials (working draft)

Two columns matter: **what the design needs** and **what you already have**.
Anything not on hand becomes the LCSC shopping list. Quantities/values firm up
after `EXISTING-HARDWARE.md` and the schematic are done.

## Core ICs / modules
| Ref | Part | Footprint | Notes | On hand? |
|-----|------|-----------|-------|----------|
| U1 | ESP32-S3-WROOM-1U (variant TBD, Q-H1) | module | MCU + WiFi/BLE | yes |
| U2 | Winbond W25N01GVZEIT | WSON-8 6x8 | 128MB NAND, QSPI | yes |
| MK1 | INMP441 I2S mic | module/SMD | digital mic | yes |
| J1 | USB-C receptacle | SMD | power + data | yes |
| U3 | TP4056 (or PMIC) | — | LiPo charger | confirm |
| U4 | AP2112K-3.3 / MCP1700 | SOT-23-5 / SOT-23 | 3.3V LDO | confirm |
| D1 | USBLC6-2SC6 | SOT-23-6 | USB ESD/TVS | confirm |

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

## To-buy list (LCSC)
> Populated after inventory: anything marked "confirm"/"no" above plus any
> passive values you're short on. Keep this list paste-ready for an LCSC cart.

- _TBD after `EXISTING-HARDWARE.md` is filled._
