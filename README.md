# Listener

An all-day, low-friction AI audio wearable + local processing pipeline.

A compact single-board ESP32-S3 device captures voice memos and conversations,
caches encoded audio to on-board NAND flash, and periodically uploads to a home
server. The server transcribes locally (faster-whisper), uses an LLM to split
content into **time-sensitive actions** and **long-term context**, and delivers
results via scheduled emails/notifications. A SQLite-backed context store can be
reviewed and edited conversationally.

## Repo map

| Path | What's there |
|------|--------------|
| [`docs/PROCESS.md`](docs/PROCESS.md) | The phased plan and where we are |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | End-to-end system design |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Decision log (ADRs) + open questions |
| [`docs/hardware/`](docs/hardware/) | BOM, on-hand stock, pinout, schematic notes |
| [`docs/firmware/`](docs/firmware/) | ESP32-S3 firmware architecture |
| [`docs/homelab/`](docs/homelab/) | Ingestion, transcription, LLM split, scheduling |
| [`docs/phone/`](docs/phone/) | Provisioning + client strategy |
| `firmware/` | ESP32-S3 source (added in the firmware phase) |
| `homelab/` | Python pipeline source (added in the pipeline phase) |
| `hardware/` | EasyEDA Pro exports + JLCPCB fab files |

## Status

Phase 1 — Align & scaffold. See [`docs/PROCESS.md`](docs/PROCESS.md).
