# Listener Homelab Pipeline

Python services on **WSL2 (Ubuntu) + RTX 4070 Super**. **Fully local** for privacy
(ADR-016). Architecture + milestones: [`../docs/homelab/PIPELINE.md`](../docs/homelab/PIPELINE.md).

## One-time environment setup
Everything runs in WSL2. We use **`uv`** for packaging and keep the venv in WSL's
*native* filesystem (not `/mnt/c` — big CUDA wheels are slow/finicky on the Windows drive).

```bash
# 1. install uv (once)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. transcription venv (faster-whisper / CUDA 12)
uv venv ~/listener-venv --python 3.10
cd /mnt/c/Listener/homelab
uv pip install --python ~/listener-venv/bin/python -r requirements.txt

# 3. diarization venv (pyannote + torch / CUDA 13) — SEPARATE on purpose
uv venv ~/listener-diar --python 3.10
uv pip install --python ~/listener-diar/bin/python -r requirements-diar.txt
sudo apt install -y ffmpeg   # torchcodec needs the libav* libs
```

> **Why two venvs?** CTranslate2 (faster-whisper) needs **CUDA 12** cuDNN; torch
> (pyannote) needs **CUDA 13** cuDNN. In one venv they collide
> (`CUDNN_STATUS_SUBLIBRARY_VERSION_MISMATCH`). Separate venvs = no conflict, and
> it matches the production design (transcriber + diarizer are separate workers).
>
> GPU note: faster-whisper's cuBLAS/cuDNN come from `nvidia-*` pip wheels — **no
> system CUDA toolkit needed**; `transcribe.py` `ctypes`-preloads them (no
> `LD_LIBRARY_PATH`). Requires the NVIDIA CUDA-on-WSL driver (`nvidia-smi` works in WSL).
>
> Diarization needs a HuggingFace token (`hf auth login`) + accepting the gated
> model `pyannote/speaker-diarization-community-1`.

## H1 — transcription ✅
```bash
# fetch the sample once (gitignored):
wget -O samples/jfk.wav https://github.com/ggerganov/whisper.cpp/raw/master/samples/jfk.wav

cd /mnt/c/Listener/homelab
~/listener-venv/bin/python transcribe.py samples/jfk.wav small.en
```
Expected: the JFK quote, **RTF ≈ 0.05 (~20× real-time)** on GPU. Swap `small.en`
→ `large-v3` for production accuracy (one-word change).

## H3.5 — diarization ✅
```bash
cd /mnt/c/Listener/homelab
~/listener-diar/bin/python diarize.py samples/jfk.wav
```
Prints `SPEAKER_00 / SPEAKER_01 / ...` turns with timestamps. JFK (1 speaker) →
one speaker; use a 2-person clip to see it split. Uses `community-1` (open, but
gated — accept its HF terms once).

**Speaker-attributed transcript** (merge of transcription + diarization):
```bash
~/listener-venv/bin/python attribute.py samples/two.wav small.en
```
`attribute.py` runs both engines as subprocesses (separate venvs) and merges by
timestamp overlap → "SPEAKER_00: …". *TODO (production):* word-level attribution
(`word_timestamps=True`) so boundary-straddling segments split correctly.

## Milestone status (PIPELINE.md H1–H6)
- [x] **H1** — WSL2 + CUDA + faster-whisper transcribes a WAV
- [ ] H2 — FastAPI `/ingest` (verify HMAC, store, ACK)
- [ ] H3 — transcriber worker → SQLite
- [x] **H3.5** — pyannote diarization (community-1, GPU)
- [ ] H3.6 — ECAPA embeddings + speaker ID/cluster
- [ ] H4 — speaker-aware LLM intent split (Ollama; model from 12GB shortlist)
- [ ] H5 — APScheduler timed email + daily 6 AM summary
- [ ] H6 — PWA dashboard (label speakers, review profiles)
