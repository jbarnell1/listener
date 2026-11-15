# CLAUDE.md - Listener project harness

Read this first every session. It points to the canonical docs and the rules that
keep them from drifting or duplicating.

## What this is
All-day AI audio wearable (ESP32-S3) + Windows/WSL2 homelab pipeline (faster-whisper
→ LLM intent split → scheduled email/notifications). Repo: `jbarnell1/listener`.

## Canonical documents (don't duplicate - link to these)
| Topic | Source of truth |
|-------|-----------------|
| Why we chose X (rationale) | `docs/DECISIONS.md` (ADRs) - **single source of truth** |
| System design / data flow | `docs/ARCHITECTURE.md` |
| Phase plan / where we are | `docs/PROCESS.md` |
| Pinout / BOM / inventory | `docs/hardware/` |
| Firmware / homelab / phone | `docs/firmware/`, `docs/homelab/`, `docs/phone/` |

## Working agreement (rules)
1. **Decisions go through the `log-decision` skill.** When we settle an
   architectural choice, run it: it appends a dated ADR, closes the matching Open
   Question, and updates every doc that referenced the old choice.
2. **DECISIONS.md owns rationale.** Other docs state *what* the design is and link
   to the ADR (`see ADR-00N`) for *why*. Never restate rationale in two places.
3. **Open Questions live at the bottom of DECISIONS.md.** Close them before the
   phase that depends on them.
4. **Commit after doc changes** with a clear message; push to `origin/main`.
5. Keep `docs/hardware/EXISTING-HARDWARE.md` (on-hand stock) and the BOM to-buy
   list in sync.

6. **The repo is public (ADR-057).** Never commit household values: Wi-Fi names or
   passwords, device keys, hostnames or tailnet names, addresses, email addresses, real
   names, audio, transcripts. Device values go in `secrets.h` (gitignored), server values in
   `~/.listener.env`. Screenshots and tests use `homelab/demo_seed.py` data only.
7. **Checks before a push:** in `homelab/`, `ruff check .` and `python -m pytest` (light web
   venv plus `requirements-dev.txt`). CI runs the same, plus gitleaks.
8. **Prose style:** no em dashes in docs, commits, PR text or UI copy. Plain, specific words.

## Hard constraints (quick ref - full detail in the docs)
- Module = **ESP32-S3-WROOM-1U-N16R8** → octal PSRAM uses **GPIO33–37**, keep free.
- **You cannot charge a LiPo with an LDO** - a dedicated charge IC is required.
- **Tailscale does not run on the ESP32**; the homelab exposes signed `/ingest`.
- 128MB NAND can't hold raw audio → **encode on-device**.

## Environment
- Dev box: Windows 11 + PowerShell. Homelab pipeline target: **WSL2 (Ubuntu)**.
- Hardware flow: EasyEDA Pro → LCSC parts → JLCPCB fab. User has a large 0603 stock
  and wants step-by-step placement/wiring guidance (clean schematics, net labels -
  no criss-crossing wires).
