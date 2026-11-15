# Third-party software, models and parts

Listener's own code and documentation are under the [GNU AGPL v3.0](../LICENSE). The things
below are not part of that grant. They keep their own licenses, and you accept those when you
install or download them.

## Bundled in this repository

| File | Project | License |
|---|---|---|
| `homelab/static/htmx.min.js` | [htmx](https://htmx.org) 2.0.4 | Zero-Clause BSD |

Nothing else from another project is copied into the repository. `homelab/reflect.py` is an
independent implementation of the "reflection" idea from the Generative Agents paper (Park et
al., 2023); no code was taken from it.

## Installed by you (Python packages)

Declared in `homelab/requirements*.txt` and installed from PyPI. Check each project for the
current terms; these are the licenses as published when this file was written.

| Package | Used for | License |
|---|---|---|
| FastAPI, Starlette | dashboard and ingest endpoints | MIT, BSD-3-Clause |
| Uvicorn | ASGI server | BSD-3-Clause |
| Jinja2 | templates | BSD-3-Clause |
| httpx | assistant HTTP client | BSD-3-Clause |
| python-multipart | form parsing | Apache-2.0 |
| dateparser | due-time parsing | BSD-3-Clause |
| mcp (Model Context Protocol SDK) | assistant tool server and client | MIT |
| APScheduler | nightly jobs | MIT |
| pywebpush, py-vapid | web push alerts | MPL-2.0 |
| google-api-python-client, google-auth-* | optional Calendar and Tasks sync | Apache-2.0 |
| faster-whisper, CTranslate2 | transcription | MIT |
| WhisperX | word-level alignment | BSD-2-Clause |
| transformers | alignment models | Apache-2.0 |
| pyannote.audio | speaker diarization | MIT |
| SpeechBrain | ECAPA speaker embeddings | Apache-2.0 |
| PyTorch, torchaudio | model runtime | BSD-3-Clause |
| nvidia-cublas-cu12, nvidia-cudnn-cu12 | GPU libraries | NVIDIA proprietary license |

## Downloaded at first run (models)

| Model | Source | License and conditions |
|---|---|---|
| Whisper (`large-v3`, `small.en`) in CTranslate2 format | Hugging Face (Systran) | MIT |
| `pyannote/speaker-diarization-community-1` | Hugging Face | CC-BY-4.0. Gated: you must accept the model's conditions with your own Hugging Face account |
| `speechbrain/spkrec-ecapa-voxceleb` | Hugging Face | Apache-2.0 |
| wav2vec2 alignment model (chosen by WhisperX per language) | torchaudio or Hugging Face | depends on the model; see WhisperX |
| `qwen3:8b` (default LLM) | Ollama library | Apache-2.0 |

## Other software you run alongside

| Software | License |
|---|---|
| [Ollama](https://ollama.com) | MIT |
| ffmpeg | LGPL or GPL, depending on the build |
| Tailscale client (optional, for remote access) | BSD-3-Clause client; the coordination service is a hosted product |
| rclone (optional, offsite backup) | MIT |
| ESP32 Arduino core (firmware build) | LGPL-2.1, with ESP-IDF components under Apache-2.0 |

## Hardware

The repository does not contain schematic, PCB or enclosure files yet (see the README). The
design notes under `docs/hardware/` are original and under the project license. They refer to
commercial parts by manufacturer part number and LCSC code:

- Espressif ESP32-S3-WROOM-1U-N16R8 module
- Winbond W25N01GV SPI NAND flash
- TDK InvenSense INMP441 microphone (on a breakout module)
- TP4056 charger, AP2112K regulator, USBLC6-2SC6 ESD protection, AO3401A MOSFET

Part numbers, pinouts and datasheet values belong to their manufacturers. The footprints and
symbols used to lay out the board came from the EasyEDA and LCSC libraries, which have their
own terms. Check those before exported design files are added to this repository.
